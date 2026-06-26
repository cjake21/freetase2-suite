/*
 * tase2_hmi_agent.c
 *
 * A persistent TASE.2/ICCP client that the SCADA HMI bridge drives. It holds a
 * single association to the FreeTASE2 server and turns simple line commands on
 * stdin into real ICCP services, emitting one JSON event per line on stdout.
 * This is what makes the HMI's interactions real protocol traffic on the wire
 * rather than a local fake: every operator action becomes an MMS read/write, and
 * Station B's view is fed by the Block 2 InformationReports this agent receives.
 *
 * The bridge runs two of these:
 *   - a "Station A" writer: WRITEF/WRITEI/OPERATE/READ (the local control centre)
 *   - a "Station B" subscriber: SUBSCRIBE, then it streams report events (the
 *     remote control centre / BA whose HMI shows whatever arrives)
 *
 * Commands (one per line on stdin):
 *   SUBSCRIBE [p1 p2 ...]     define ds_hmi over the given points (default tm1,tm2,ts1,ts2),
 *                             bind DSTransferSet01, enable RBE+integrity
 *   WRITEF <item> <float>     write a float Value (tm1, tm2)        e.g. WRITEF tm1 137.5
 *   WRITEI <item> <int>       write an integer Value (ts1, ts2)     e.g. WRITEI ts1 0
 *   WRITEQ <item> <isFloat> <value> <qualityByte> <unixSec>   write Value + quality + time tag
 *   SELECT <device>                Block 5 select-before-operate: select  e.g. SELECT plc1_brk_ctl
 *   CANCEL <device>                Block 5 deselect / cancel selection
 *   OPERATE <device> <cmd> [tag]   Block 5 operate (int Command + Tag)   e.g. OPERATE plc1_brk_ctl 1 hmi
 *   SETPOINT <device> <val> [tag]  Block 5 operate (float Command + Tag) e.g. SETPOINT plc1_avr_ctl 1.05 hmi
 *   READ <item>               read a point's Value                  e.g. READ tm1
 *   SNAPSHOT [p1 p2 ...]      read Block 1 metadata + the given points (or the current set)
 *   QUIT
 *
 * Events (one JSON object per line on stdout):
 *   {"ev":"online","host":"..","port":N,"domain":".."}
 *   {"ev":"snapshot","version":"..","features":"..","blt":"..","next_ts":"..",
 *    "tm1":..,"tm2":..,"ts1":..,"ts2":..}
 *   {"ev":"read","item":"tm1","value":..}
 *   {"ev":"write","item":"tm1$Value","value":..,"err":N}
 *   {"ev":"subscribed","dataset":"ds_hmi","transferset":"DSTransferSet01"}
 *   {"ev":"report","ts":"DSTransferSet01","time":"..","cond":N,"tm1":..,"tm2":..,"ts1":..,"ts2":..}
 *   {"ev":"error","msg":".."}
 *
 * usage: tase2_hmi_agent <host> <port> [domain]
 * GPL-3.0.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <sys/select.h>

#include "mms_client_connection.h"
#include "tls_config.h"            /* TLS / Secure ICCP client (optional, env-driven) */
#include "mms_value.h"
#include "linked_list.h"
#include "hal_thread.h"

static volatile int g_running = 1;
static const char*  g_dom = "TestDomain";

/* The point set this agent subscribes to / snapshots. Populated by SUBSCRIBE
 * (with an explicit list) or defaulted to the lab's tm1/tm2/ts1/ts2 set. The
 * report handler maps data set members back to these names by position, so this
 * must match the order ds_hmi is defined in. */
#define MAX_PTS 256
/* A single TASE.2 data set / transfer-set report tops out around 64 members in
 * libIEC61850, so the point set is split across several transfer sets
 * (DSTransferSet01..08), each carrying up to CHUNK_PTS points. This is also how a
 * real ICCP feed groups telemetry into multiple transfer sets. */
#define CHUNK_PTS 50
static char g_pts[MAX_PTS][64];
static int  g_nPts = 0;

static void
setDefaultPoints(void)
{
    static const char* def[4] = {"tm1", "tm2", "ts1", "ts2"};
    g_nPts = 4;
    for (int i = 0; i < 4; i++)
        snprintf(g_pts[i], sizeof(g_pts[0]), "%s", def[i]);
}

static void sigHandler(int s) { (void)s; g_running = 0; }

/* Print a JSON string value with the few escapes we can actually hit (quotes,
 * backslashes, control chars). Tags and object names are simple, but be safe. */
static void
emitJsonString(const char* s)
{
    putchar('"');
    for (const char* p = s; p && *p; p++) {
        unsigned char c = (unsigned char) *p;
        if (c == '"' || c == '\\') { putchar('\\'); putchar(c); }
        else if (c < 0x20)         { printf("\\u%04x", c); }
        else                        putchar(c);
    }
    putchar('"');
}

/* Read element 0 ("Value") of an indication-point structure as a JSON number. */
static void
emitPointValue(MmsValue* v)
{
    if (v == NULL) { printf("null"); return; }
    MmsValue* el = (MmsValue_getType(v) == MMS_STRUCTURE) ? MmsValue_getElement(v, 0) : v;
    if (el == NULL) { printf("null"); return; }
    switch (MmsValue_getType(el)) {
        case MMS_FLOAT:   printf("%.6g", MmsValue_toFloat(el)); break;
        case MMS_INTEGER: printf("%d", MmsValue_toInt32(el)); break;
        default:          printf("null"); break;
    }
}

static MmsValue*
readVar(MmsConnection con, const char* domain, const char* item)
{
    MmsError err;
    return MmsConnection_readVariable(con, &err, domain, item);
}

/* Quality byte (element 1, the TASE.2 flags bitstring) of an indication-point
 * structure, or 0 if absent. */
static int
memberQuality(MmsValue* m)
{
    if (m && MmsValue_getType(m) == MMS_STRUCTURE && MmsValue_getArraySize(m) >= 2) {
        MmsValue* q = MmsValue_getElement(m, 1);
        if (q && MmsValue_getType(q) == MMS_BIT_STRING)
            return (int) MmsValue_getBitStringAsInteger(q);
    }
    return 0;
}

/* Time tag (element 2, Unix seconds) of an indication-point structure, or 0. */
static long
memberTime(MmsValue* m)
{
    if (m && MmsValue_getType(m) == MMS_STRUCTURE && MmsValue_getArraySize(m) >= 3) {
        MmsValue* t = MmsValue_getElement(m, 2);
        if (t && MmsValue_getType(t) == MMS_INTEGER)
            return (long) MmsValue_toInt32(t);
    }
    return 0;
}

/* Block 2 report arriving: header is {name, time, conditions} then the data set
 * members in subscribed point order. Each member is { Value, Flags, TimeStamp }.
 * We emit the values inline, plus parallel "q" (quality byte) and "t" (Unix time)
 * objects so the bridge carries real per-point quality and acquisition time. */
static void
reportHandler(void* parameter, char* domainName, char* variableListName,
              MmsValue* value, bool isVariableListName)
{
    int n = MmsValue_getArraySize(value);

    /* The members in this report belong to one transfer set (DSTransferSet0N),
     * which carries chunk N-1 of the point set, i.e. points starting at
     * (N-1)*CHUNK_PTS. Recover that base from the transfer-set name so each report
     * maps its members back to the right point names. */
    int base = 0;
    if (variableListName) {
        int v = 0, seen = 0;
        for (const char* c = variableListName; *c; c++)
            if (*c >= '0' && *c <= '9') { v = v * 10 + (*c - '0'); seen = 1; }
        if (seen && v >= 1) base = (v - 1) * CHUNK_PTS;
    }

    printf("{\"ev\":\"report\",\"ts\":");
    emitJsonString(variableListName ? variableListName : "?");

    if (n >= 2) {
        char buf[64];
        MmsValue_printToBuffer(MmsValue_getElement(value, 1), buf, sizeof(buf));
        printf(",\"time\":");
        emitJsonString(buf);
    }
    if (n >= 3)
        printf(",\"cond\":%d", MmsValue_toInt32(MmsValue_getElement(value, 2)));

    /* members start at index 3, mapped to this transfer set's point chunk */
    for (int i = 3; i < n && (base + i - 3) < g_nPts; i++) {
        printf(",\"%s\":", g_pts[base + i - 3]);
        emitPointValue(MmsValue_getElement(value, i));
    }
    printf(",\"q\":{");
    for (int i = 3; i < n && (base + i - 3) < g_nPts; i++)
        printf("%s\"%s\":%d", (i > 3) ? "," : "", g_pts[base + i - 3],
               memberQuality(MmsValue_getElement(value, i)));
    printf("},\"t\":{");
    for (int i = 3; i < n && (base + i - 3) < g_nPts; i++)
        printf("%s\"%s\":%ld", (i > 3) ? "," : "", g_pts[base + i - 3],
               memberTime(MmsValue_getElement(value, i)));
    printf("}}\n");
    fflush(stdout);
}

static void
doSubscribe(MmsConnection con)
{
    MmsError err;
    int nsets = (g_nPts + CHUNK_PTS - 1) / CHUNK_PTS;
    if (nsets < 1) nsets = 1;
    if (nsets > 8) nsets = 8;          /* the server provides DSTransferSet01..08 */

    MmsConnection_setInformationReportHandler(con, reportHandler, NULL);

    for (int c = 0; c < nsets; c++) {
        int start = c * CHUNK_PTS;
        int end = start + CHUNK_PTS;
        if (end > g_nPts) end = g_nPts;
        char ds[32], ts[24], item[48];
        snprintf(ds, sizeof ds, "ds_hmi_%d", c);
        snprintf(ts, sizeof ts, "DSTransferSet%02d", c + 1);

        LinkedList dsVars = LinkedList_create();
        for (int i = start; i < end; i++)
            LinkedList_add(dsVars,
                MmsVariableAccessSpecification_create(strdup(g_dom), strdup(g_pts[i])));
        MmsConnection_deleteNamedVariableList(con, &err, g_dom, ds);
        MmsConnection_defineNamedVariableList(con, &err, g_dom, ds, dsVars);
        LinkedList_destroyDeep(dsVars,
            (LinkedListValueDeleteFunction) MmsVariableAccessSpecification_destroy);

        snprintf(item, sizeof item, "%s$DataSetName", ts);
        MmsConnection_writeVariable(con, &err, g_dom, item, MmsValue_newVisibleString(ds));
        snprintf(item, sizeof item, "%s$Interval", ts);
        MmsConnection_writeVariable(con, &err, g_dom, item, MmsValue_newIntegerFromInt32(5));
        snprintf(item, sizeof item, "%s$DSConditionsRequested", ts);
        MmsConnection_writeVariable(con, &err, g_dom, item, MmsValue_newIntegerFromInt32(0x06));
        snprintf(item, sizeof item, "%s$RBE", ts);
        MmsConnection_writeVariable(con, &err, g_dom, item, MmsValue_newBoolean(true));
        snprintf(item, sizeof item, "%s$Status", ts);
        MmsConnection_writeVariable(con, &err, g_dom, item, MmsValue_newIntegerFromInt32(1));
    }

    printf("{\"ev\":\"subscribed\",\"dataset\":\"ds_hmi\",\"transferset\":\"DSTransferSet01\",\"sets\":%d}\n",
           nsets);
    fflush(stdout);
}

static void
doWrite(MmsConnection con, const char* item, MmsValue* v)
{
    MmsError err;
    char full[80];
    snprintf(full, sizeof(full), "%s$Value", item);
    MmsConnection_writeVariable(con, &err, g_dom, full, v);
    MmsValue_delete(v);
    printf("{\"ev\":\"write\",\"item\":");
    emitJsonString(full);
    printf(",\"err\":%d}\n", err);
    fflush(stdout);
}

/* Block 5 operate on a device control object: write Tag then Command. The
 * command is an integer for a discrete control (e.g. breaker), a float for an
 * analog setpoint (isSetpoint). */
/* Write an indication point with quality and time tag: Value, Flags (the TASE.2
 * quality byte, sent as an 8-bit bitstring), and TimeStamp (Unix seconds). This is
 * how the ingestion gateway carries field quality and acquisition time up. */
static void
doWriteQ(MmsConnection con, const char* item, int isFloat, const char* val,
         int qual, long ts)
{
    MmsError err;
    char full[96];

    snprintf(full, sizeof(full), "%s$Value", item);
    MmsConnection_writeVariable(con, &err, g_dom, full,
        isFloat ? MmsValue_newFloat((float) atof(val))
                : MmsValue_newIntegerFromInt32(atoi(val)));

    snprintf(full, sizeof(full), "%s$Flags", item);
    MmsValue* q = MmsValue_newBitString(8);
    MmsValue_setBitStringFromInteger(q, (uint32_t) qual);
    MmsConnection_writeVariable(con, &err, g_dom, full, q);
    MmsValue_delete(q);

    snprintf(full, sizeof(full), "%s$TimeStamp", item);
    MmsConnection_writeVariable(con, &err, g_dom, full,
        MmsValue_newIntegerFromInt32((int32_t) ts));

    printf("{\"ev\":\"write\",\"item\":");
    emitJsonString(item);
    printf(",\"q\":%d,\"err\":%d}\n", qual, err);
    fflush(stdout);
}

static void
doOperate(MmsConnection con, const char* device, double command, const char* tag, bool isSetpoint)
{
    MmsError err;
    char item[96];
    snprintf(item, sizeof(item), "%s$Tag", device);
    MmsConnection_writeVariable(con, &err, g_dom, item, MmsValue_newVisibleString(tag));
    snprintf(item, sizeof(item), "%s$Command", device);
    MmsValue* cmd = isSetpoint ? MmsValue_newFloat((float) command)
                               : MmsValue_newIntegerFromInt32((int) command);
    MmsConnection_writeVariable(con, &err, g_dom, item, cmd);
    printf("{\"ev\":\"operate\",\"device\":");
    emitJsonString(device);
    if (isSetpoint) printf(",\"command\":%.6g", command);
    else            printf(",\"command\":%d", (int) command);
    printf(",\"tag\":");
    emitJsonString(tag);
    printf(",\"err\":%d}\n", err);
    fflush(stdout);
}

/* Select-before-operate: write the device's SBO register (1 = select, 0 = cancel).
 * The server grants or denies; the err code tells the caller whether the select
 * was accepted. */
static void
doSelect(MmsConnection con, const char* device, int select)
{
    MmsError err;
    char item[96];
    snprintf(item, sizeof(item), "%s$SBO", device);
    MmsConnection_writeVariable(con, &err, g_dom, item, MmsValue_newIntegerFromInt32(select ? 1 : 0));
    printf("{\"ev\":\"%s\",\"device\":", select ? "select" : "cancel");
    emitJsonString(device);
    printf(",\"err\":%d}\n", err);
    fflush(stdout);
}

static void
doRead(MmsConnection con, const char* item)
{
    MmsValue* v = readVar(con, g_dom, item);
    printf("{\"ev\":\"read\",\"item\":");
    emitJsonString(item);
    printf(",\"value\":");
    emitPointValue(v);
    printf("}\n");
    fflush(stdout);
    if (v) MmsValue_delete(v);
}

static void
doSnapshot(MmsConnection con)
{
    char buf[128];
    printf("{\"ev\":\"snapshot\"");

    MmsValue* v = readVar(con, NULL, "TASE2_Version");
    if (v) { MmsValue_printToBuffer(v, buf, sizeof(buf)); printf(",\"version\":"); emitJsonString(buf); MmsValue_delete(v); }
    v = readVar(con, NULL, "Supported_Features");
    if (v) { MmsValue_printToBuffer(v, buf, sizeof(buf)); printf(",\"features\":"); emitJsonString(buf); MmsValue_delete(v); }
    v = readVar(con, g_dom, "Bilateral_Table_ID");
    if (v) { MmsValue_printToBuffer(v, buf, sizeof(buf)); printf(",\"blt\":"); emitJsonString(buf); MmsValue_delete(v); }
    v = readVar(con, g_dom, "Next_DSTransfer_Set");
    if (v) { MmsValue_printToBuffer(v, buf, sizeof(buf)); printf(",\"next_ts\":"); emitJsonString(buf); MmsValue_delete(v); }

    for (int i = 0; i < g_nPts; i++) {
        v = readVar(con, g_dom, g_pts[i]);
        printf(",\"%s\":", g_pts[i]);
        emitPointValue(v);
        if (v) MmsValue_delete(v);
    }
    printf("}\n");
    fflush(stdout);
}

/* Trim trailing newline / CR in place. */
static void
chomp(char* s)
{
    size_t n = strlen(s);
    while (n && (s[n - 1] == '\n' || s[n - 1] == '\r')) s[--n] = '\0';
}

static void
handleCommand(MmsConnection con, char* line)
{
    char* cmd = strtok(line, " \t");
    if (cmd == NULL) return;

    if (!strcmp(cmd, "SUBSCRIBE")) {
        /* "SUBSCRIBE p1 p2 ..." sets the point set; bare "SUBSCRIBE" keeps the
         * current/default set. The data set is rebuilt from this list. */
        char* p = strtok(NULL, " \t");
        if (p) {
            g_nPts = 0;
            while (p && g_nPts < MAX_PTS) {
                snprintf(g_pts[g_nPts++], sizeof(g_pts[0]), "%s", p);
                p = strtok(NULL, " \t");
            }
        }
        doSubscribe(con);
    } else if (!strcmp(cmd, "WRITEF")) {
        char* item = strtok(NULL, " \t");
        char* val  = strtok(NULL, " \t");
        if (item && val) doWrite(con, item, MmsValue_newFloat((float) atof(val)));
    } else if (!strcmp(cmd, "WRITEI")) {
        char* item = strtok(NULL, " \t");
        char* val  = strtok(NULL, " \t");
        if (item && val) doWrite(con, item, MmsValue_newIntegerFromInt32(atoi(val)));
    } else if (!strcmp(cmd, "WRITEQ")) {
        /* WRITEQ <point> <isFloat 0|1> <value> <qualityByte> <unixSeconds> */
        char* item = strtok(NULL, " \t");
        char* isf  = strtok(NULL, " \t");
        char* val  = strtok(NULL, " \t");
        char* qual = strtok(NULL, " \t");
        char* ts   = strtok(NULL, " \t");
        if (item && isf && val && qual && ts)
            doWriteQ(con, item, atoi(isf), val, atoi(qual), atol(ts));
    } else if (!strcmp(cmd, "OPERATE")) {
        /* OPERATE <device> <intCommand> [tag] */
        char* dev = strtok(NULL, " \t");
        char* val = strtok(NULL, " \t");
        char* tag = strtok(NULL, " \t");
        if (dev && val) doOperate(con, dev, atoi(val), tag ? tag : "hmi-op", false);
    } else if (!strcmp(cmd, "SETPOINT")) {
        /* SETPOINT <device> <floatCommand> [tag] */
        char* dev = strtok(NULL, " \t");
        char* val = strtok(NULL, " \t");
        char* tag = strtok(NULL, " \t");
        if (dev && val) doOperate(con, dev, atof(val), tag ? tag : "hmi-sp", true);
    } else if (!strcmp(cmd, "SELECT")) {
        char* dev = strtok(NULL, " \t");
        if (dev) doSelect(con, dev, 1);
    } else if (!strcmp(cmd, "CANCEL")) {
        char* dev = strtok(NULL, " \t");
        if (dev) doSelect(con, dev, 0);
    } else if (!strcmp(cmd, "READ")) {
        char* item = strtok(NULL, " \t");
        if (item) doRead(con, item);
    } else if (!strcmp(cmd, "SNAPSHOT")) {
        /* optional "SNAPSHOT p1 p2 ..." sets the point set to read */
        char* p = strtok(NULL, " \t");
        if (p) {
            g_nPts = 0;
            while (p && g_nPts < MAX_PTS) {
                snprintf(g_pts[g_nPts++], sizeof(g_pts[0]), "%s", p);
                p = strtok(NULL, " \t");
            }
        }
        doSnapshot(con);
    } else if (!strcmp(cmd, "QUIT")) {
        g_running = 0;
    }
}

int
main(int argc, char** argv)
{
    const char* host = (argc > 1) ? argv[1] : "127.0.0.1";
    int port         = (argc > 2) ? atoi(argv[2]) : 102;
    g_dom            = (argc > 3) ? argv[3] : "TestDomain";

    setDefaultPoints();

    signal(SIGINT, sigHandler);
    signal(SIGTERM, sigHandler);
    setvbuf(stdout, NULL, _IOLBF, 0);

    /* Optional TLS / Secure ICCP client, enabled by environment so the bridge and
     * ingest gateway can drive a hardened (mutual-TLS) server without new flags:
     *   TASE2_TLS=1  TASE2_TLS_CERT=..  TASE2_TLS_KEY=..  TASE2_TLS_CA=.. */
    MmsConnection con;
    const char* tlsEnv = getenv("TASE2_TLS");
    if (tlsEnv && atoi(tlsEnv) != 0) {
        TLSConfiguration tls = TLSConfiguration_create();
        const char* cert = getenv("TASE2_TLS_CERT");
        const char* key  = getenv("TASE2_TLS_KEY");
        const char* ca   = getenv("TASE2_TLS_CA");
        if (cert) TLSConfiguration_setOwnCertificateFromFile(tls, cert);
        if (key)  TLSConfiguration_setOwnKeyFromFile(tls, key, NULL);
        if (ca) {
            TLSConfiguration_addCACertificateFromFile(tls, ca);
            TLSConfiguration_setChainValidation(tls, true);
        }
        con = MmsConnection_createSecure(tls);
    } else {
        con = MmsConnection_create();
    }
    MmsError err;

    if (!MmsConnection_connect(con, &err, host, port)) {
        printf("{\"ev\":\"error\",\"msg\":\"connect failed (err=%d)\"}\n", err);
        fflush(stdout);
        MmsConnection_destroy(con);
        return 1;
    }
    printf("{\"ev\":\"online\",\"host\":");
    emitJsonString(host);
    printf(",\"port\":%d,\"domain\":", port);
    emitJsonString(g_dom);
    printf("}\n");
    fflush(stdout);

    char line[8192];   /* SUBSCRIBE/SNAPSHOT can carry 100+ point names on one line */
    while (g_running) {
        /* Pump the MMS stack so unsolicited reports are delivered promptly. */
        MmsConnection_tick(con);

        /* Wait briefly for a command, but stay responsive to reports. */
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(STDIN_FILENO, &rfds);
        struct timeval tv = { .tv_sec = 0, .tv_usec = 150000 };
        int r = select(STDIN_FILENO + 1, &rfds, NULL, NULL, &tv);
        if (r > 0 && FD_ISSET(STDIN_FILENO, &rfds)) {
            /* Drain every command queued this wake-up rather than one per loop.
             * The gateway emits a burst (one WRITEQ per point plus control reads)
             * each poll; processing only one per iteration lets that burst build
             * an ever-growing backlog, so values lag the field by many seconds.
             * Draining keeps the read-back current. */
            do {
                if (fgets(line, sizeof(line), stdin) == NULL) { g_running = 0; break; } /* EOF: bridge closed */
                chomp(line);
                handleCommand(con, line);
                FD_ZERO(&rfds);
                FD_SET(STDIN_FILENO, &rfds);
                tv.tv_sec = 0; tv.tv_usec = 0;   /* poll: is another command already waiting? */
            } while (g_running &&
                     select(STDIN_FILENO + 1, &rfds, NULL, NULL, &tv) > 0 &&
                     FD_ISSET(STDIN_FILENO, &rfds));
        }
    }

    MmsConnection_destroy(con);
    return 0;
}
