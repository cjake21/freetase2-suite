/*
 * tase2_server.c
 *
 * A small TASE.2/ICCP server built on libIEC61850's low-level MMS server API.
 *
 * libIEC61850 comes with an IEC 61850 server and an MMS client but no TASE.2
 * server, and the TASE.2 servers that do exist are commercial. This fills that
 * gap. It stands up a TASE.2/ICCP object model over the usual
 * TPKT/COTP/Session/Presentation/ACSE/MMS stack on TCP/102, so a TASE.2 client
 * can associate and run real ICCP services against it. Handy as a target for
 * SCADA/OT protocol testing and for checking IDS and parser tooling.
 *
 * It covers the common conformance blocks:
 *   Block 1  association, the VCC/ICC objects, data values, data sets and the
 *            transfer-set objects, and the bilateral table.
 *   Block 2  report-by-exception and integrity reporting of transfer sets, sent
 *            as unconfirmed MMS InformationReport PDUs.
 *   Block 5  a device control point you can select and operate.
 *
 * Objects live at two scopes. VMD scope (read with domain = NULL) holds
 * TASE2_Version and Supported_Features. The ICC domain holds everything else:
 * Bilateral_Table_ID, Next_DSTransfer_Set, the transfer-set status variables,
 * the indication points (tm1/tm2 RealQ, ts1/ts2 StateQ), the DSTransferSetNN
 * objects, and the dev1 control point.
 *
 * One thing to keep in mind: TASE.2 on the wire is just MMS (ISO 9506). There
 * is no separate TASE.2 PDU. What makes a capture TASE.2 is this object model
 * and the transfer-set / report behaviour layered on top of MMS. The indication
 * point and transfer-set value encodings here follow the common TASE.2
 * conventions rather than the full IEC 60870-6-802 type catalogue.
 *
 * Build it with the Makefile in this directory. libIEC61850 needs to be built
 * with CONFIG_MMS_SUPPORT_VMD_SCOPE_NAMED_VARIABLES=1. GPL-3.0.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <time.h>
#include <math.h>

/* libIEC61850 public MMS headers */
#include "mms_server.h"
#include "mms_value.h"
#include "mms_type_spec.h"
#include "linked_list.h"
#include "hal_thread.h"
#include "tls_config.h"             /* TLS / Secure ICCP (optional, -T) */

/* libIEC61850 internal MMS server headers (not installed; included from the
 * source tree via -I in the Makefile). These expose the low-level model and
 * server lifecycle that IedServer is normally built on. */
#include "mms_device_model.h"        /* MmsDevice / MmsDomain / MmsVariableSpecification */
#include "mms_server_libinternal.h"  /* MmsServer lifecycle, handlers, value cache */
#include "mms_server_connection.h"   /* MmsServerConnection_sendInformationReport* */
#include "mms_named_variable_list.h" /* data set (named variable list) iteration */

/* Configuration */

#define TASE2_DEFAULT_PORT      102
#define TASE2_DEFAULT_DOMAIN    "TestDomain"
#define TASE2_DEFAULT_BLT_ID    "TestBilTab"
#define TASE2_VERSION_MAJOR     2000
#define TASE2_VERSION_MINOR     8
#define MAX_TRANSFER_SETS       8
#define SUPPORTED_FEATURES_BITS 16   /* CBB support bitstring */

/* DSConditions (report trigger) bit values per TASE.2 */
#define DSCOND_INTERVAL_TIMEOUT 0x01
#define DSCOND_INTEGRITY        0x02
#define DSCOND_OBJECT_CHANGE    0x04
#define DSCOND_OPERATOR_REQUEST 0x08
#define DSCOND_OTHER_EXTERNAL   0x10

typedef struct {
    const char* bindIp;        /* NULL = all interfaces */
    int         port;
    const char* domainName;
    const char* bltId;
    int         integritySeconds;
    int         tls;                /* -T : serve over TLS (Secure ICCP) */
    const char* certFile;           /* -C : server certificate (PEM) */
    const char* keyFile;            /* -K : server private key (PEM) */
    const char* caFile;             /* -A : CA used to validate client certs */
    int         overrideHoldSeconds;/* -o : hold a written tm/ts value this long
                                     *      before simulation resumes (0 = never
                                     *      override; simulation always wins) */
    const char* pointsFile;         /* -P : point list "name type(real|state)"
                                     *      per line; NULL = built-in tm/ts set */
    int         noSim;              /* -n : disable the synthetic value source so
                                     *      points change only via writes (real
                                     *      mode: values come from ingestion) */
    char*       allow[16];          /* -L : peers (IPs) permitted to write/operate;
                                     *      empty = allow all (range default) */
    int         allowCount;
    const char* bltFile;            /* -B : bilateral table (per-peer data scoping) */
} Tase2Config;

/* Server state */

/* One DS Transfer Set the server tracks. The standard exposes these as MMS
 * named variables (a structure) the client writes to in order to enable
 * reporting of a named variable list (data set). */
typedef struct {
    char     name[32];          /* e.g. "DSTransferSet01" */
    char     dataSetName[129];  /* bound named variable list (data set) */
    bool     enabled;
    int      interval;          /* integrity period (s); 0 => use default */
    int      dsConditions;      /* requested condition mask */
    uint32_t reportsSent;
} TransferSet;

static MmsServer       g_server = NULL;
static MmsDevice*      g_device = NULL;
static MmsDomain*      g_domain = NULL;
static Tase2Config     g_cfg;
static volatile int    g_running = 1;

static Semaphore       g_lock;               /* guards the lists below */
static LinkedList      g_connections = NULL; /* <MmsServerConnection> */
static TransferSet     g_transferSets[MAX_TRANSFER_SETS];

/* Dynamic indication-point registry. The point model is no longer the fixed
 * tm1/tm2/ts1/ts2 set: it is built at startup from the points file (-P) or, if
 * none is given, from the built-in default set below. Each point is a RealQ
 * (float Value) or StateQ (int Value) structure { Value, Flags } whose Value we
 * mutate in the cache under the model lock. This is what lets the publisher carry
 * an arbitrary per-station tag list instead of a demo's worth of points. */
#define MAX_POINTS 256

/* control kind for a point */
#define CTL_NONE     0
#define CTL_DISCRETE 1   /* int command (e.g. breaker open/close) */
#define CTL_SETPOINT 2   /* float command (e.g. analog setpoint) */

typedef struct {
    char      name[64];
    int       isReal;        /* 1 = RealQ (float), 0 = StateQ (int) */
    MmsValue* cell;          /* { Value, Flags } structure in the cache */
    /* False-data-injection / ingest override: a client write to this point's
     * Value holds it (suspending simulation for it) until this wall-clock time,
     * so the written value propagates to the BA in reports before the sim
     * resumes. 0 = not overridden. */
    uint64_t  overrideUntil;
    /* Control: if this point is commandable, the server also publishes a Block 5
     * device control object named ctlName = "<name>_ctl" { Command, Tag, Status }.
     * The HMI operates it; the ingest reads Command and writes it down to the PLC.
     * This is a separate object from the monitoring point so operator commands and
     * field read-backs never overwrite each other. */
    int       control;       /* CTL_NONE | CTL_DISCRETE | CTL_SETPOINT */
    char      ctlName[72];
    MmsValue* ctlCell;       /* { Command, Tag, Status, SBO } structure in the cache */
    /* Select-before-operate. When ctlSbo is set, a client must select the device
     * (write SBO=1) before an operate (write Command) is accepted, and only the
     * selecting connection may operate, within the select timeout. */
    int       ctlSbo;        /* 1 = SBO device, 0 = direct operate */
    void*     ctlSelBy;      /* MmsServerConnection that holds the selection */
    uint64_t  ctlSelExpiry;  /* wall-clock ms when the selection lapses */
} Point;

#define SBO_SELECT_TIMEOUT_MS 30000

static Point g_points[MAX_POINTS];
static int   g_nPoints = 0;

static MmsValue* g_tsTimeStamp = NULL;        /* Transfer_Set_Time_Stamp */
static MmsValue* g_dsConditionsDetected = NULL;

static int
pointIndex(const char* base)
{
    for (int i = 0; i < g_nPoints; i++)
        if (!strcmp(base, g_points[i].name)) return i;
    return -1;
}

/* Build the point registry from the -P file, or fall back to the built-in
 * tm1/tm2/ts1/ts2 set so the server behaves exactly like the lab baseline when
 * run with no config. File format: one "<name> <type>" per line, type is
 * "real" or "state"; blank lines and lines starting with '#' are ignored. */
static void
loadPoints(void)
{
    g_nPoints = 0;

    if (g_cfg.pointsFile == NULL) {
        static const struct { const char* n; int real; } def[] = {
            {"tm1", 1}, {"tm2", 1}, {"ts1", 0}, {"ts2", 0},
        };
        for (size_t i = 0; i < sizeof(def) / sizeof(def[0]); i++) {
            snprintf(g_points[g_nPoints].name, sizeof(g_points[0].name), "%s", def[i].n);
            g_points[g_nPoints].isReal = def[i].real;
            g_points[g_nPoints].control = CTL_NONE;
            g_nPoints++;
        }
        return;
    }

    FILE* f = fopen(g_cfg.pointsFile, "r");
    if (f == NULL) {
        fprintf(stderr, "[tase2] cannot open points file %s\n", g_cfg.pointsFile);
        exit(1);
    }
    char line[128];
    while (fgets(line, sizeof(line), f)) {
        char name[64], type[16], ctl[16], mode[16];
        int got = sscanf(line, " %63s %15s %15s %15s", name, type, ctl, mode);
        if (got < 2 || name[0] == '#')
            continue;
        if (g_nPoints >= MAX_POINTS) {
            fprintf(stderr, "[tase2] too many points (max %d)\n", MAX_POINTS);
            exit(1);
        }
        int isReal;
        if (!strcmp(type, "real"))       isReal = 1;
        else if (!strcmp(type, "state")) isReal = 0;
        else {
            fprintf(stderr, "[tase2] point %s: bad type '%s' (want real|state)\n", name, type);
            exit(1);
        }
        int control = CTL_NONE;
        if (got >= 3) {
            if (!strcmp(ctl, "discrete"))      control = CTL_DISCRETE;
            else if (!strcmp(ctl, "setpoint")) control = CTL_SETPOINT;
            else if (strcmp(ctl, "-") != 0) {
                fprintf(stderr, "[tase2] point %s: bad control '%s'\n", name, ctl);
                exit(1);
            }
        }
        Point* p = &g_points[g_nPoints];
        snprintf(p->name, sizeof(p->name), "%s", name);
        p->isReal = isReal;
        p->control = control;
        p->ctlSbo = (control != CTL_NONE && got >= 4 && !strcmp(mode, "sbo")) ? 1 : 0;
        if (control != CTL_NONE)
            snprintf(p->ctlName, sizeof(p->ctlName), "%s_ctl", name);
        g_nPoints++;
    }
    fclose(f);
    if (g_nPoints == 0) {
        fprintf(stderr, "[tase2] points file %s defined no points\n", g_cfg.pointsFile);
        exit(1);
    }
}

/* Small helpers for building MmsVariableSpecification type trees */

static MmsVariableSpecification*
specLeaf(const char* name, MmsType type, int sizeParam)
{
    MmsVariableSpecification* s =
        (MmsVariableSpecification*) calloc(1, sizeof(MmsVariableSpecification));
    s->name = name ? strdup(name) : NULL;
    s->type = type;
    switch (type) {
        case MMS_INTEGER:       s->typeSpec.integer = sizeParam; break;
        case MMS_UNSIGNED:      s->typeSpec.unsignedInteger = sizeParam; break;
        case MMS_BIT_STRING:    s->typeSpec.bitString = sizeParam; break;
        case MMS_VISIBLE_STRING:s->typeSpec.visibleString = sizeParam; break;
        case MMS_OCTET_STRING:  s->typeSpec.octetString = sizeParam; break;
        case MMS_BINARY_TIME:   s->typeSpec.binaryTime = sizeParam; break;
        case MMS_FLOAT:
            s->typeSpec.floatingpoint.exponentWidth = 8;
            s->typeSpec.floatingpoint.formatWidth = 32;
            break;
        default: break;
    }
    return s;
}

static MmsVariableSpecification*
specStruct(const char* name, int nElems)
{
    MmsVariableSpecification* s =
        (MmsVariableSpecification*) calloc(1, sizeof(MmsVariableSpecification));
    s->name = name ? strdup(name) : NULL;
    s->type = MMS_STRUCTURE;
    s->typeSpec.structure.elementCount = nElems;
    s->typeSpec.structure.elements =
        (MmsVariableSpecification**) calloc(nElems, sizeof(MmsVariableSpecification*));
    return s;
}

/* RealQ indication point with quality and time tag (IEC 60870-6-802 Data_RealQTimeTag
 * shape): structure { Value: float, Flags: bitstring(8), TimeStamp: int (Unix sec) }.
 * Flags carry the TASE.2 quality byte (validity, current source, normal value,
 * time-stamp quality). */
static MmsVariableSpecification*
specRealQ(const char* name)
{
    MmsVariableSpecification* s = specStruct(name, 3);
    s->typeSpec.structure.elements[0] = specLeaf("Value", MMS_FLOAT, 0);
    s->typeSpec.structure.elements[1] = specLeaf("Flags", MMS_BIT_STRING, 8);
    s->typeSpec.structure.elements[2] = specLeaf("TimeStamp", MMS_INTEGER, 32);
    return s;
}

/* StateQ indication point with quality and time tag:
 * structure { Value: int(8), Flags: bitstring(8), TimeStamp: int (Unix sec) } */
static MmsVariableSpecification*
specStateQ(const char* name)
{
    MmsVariableSpecification* s = specStruct(name, 3);
    s->typeSpec.structure.elements[2] = specLeaf("TimeStamp", MMS_INTEGER, 32);
    s->typeSpec.structure.elements[0] = specLeaf("Value", MMS_INTEGER, 8);
    s->typeSpec.structure.elements[1] = specLeaf("Flags", MMS_BIT_STRING, 8);
    return s;
}

/* Block 5 device control object: structure { Command, Tag, Status }. Command is
 * an integer for discrete controls or a float for analog setpoints. */
/* Block 5 device control object: { Command, Tag, Status, SBO }. Command is an
 * integer for discrete controls or a float for analog setpoints. SBO is the
 * select register for select-before-operate devices (write 1 to select). */
static MmsVariableSpecification*
specControl(const char* name, int isSetpoint)
{
    MmsVariableSpecification* s = specStruct(name, 4);
    s->typeSpec.structure.elements[0] =
        isSetpoint ? specLeaf("Command", MMS_FLOAT, 0) : specLeaf("Command", MMS_INTEGER, 8);
    s->typeSpec.structure.elements[1] = specLeaf("Tag", MMS_VISIBLE_STRING, 32);
    s->typeSpec.structure.elements[2] = specLeaf("Status", MMS_INTEGER, 8);
    s->typeSpec.structure.elements[3] = specLeaf("SBO", MMS_INTEGER, 8);
    return s;
}

/* A DS Transfer Set object: structure of the standard TASE.2 attributes. */
static MmsVariableSpecification*
specTransferSet(const char* name)
{
    const char* fields[] = {
        "DataSetName", "StartTime", "Interval", "TLE", "BufferTime",
        "IntegrityCheck", "BlockData", "Critical", "RBE",
        "AllChangesReported", "Status", "EventCodeRequested", "DSConditionsRequested"
    };
    int n = (int)(sizeof(fields) / sizeof(fields[0]));
    MmsVariableSpecification* s = specStruct(name, n);
    s->typeSpec.structure.elements[0]  = specLeaf("DataSetName", MMS_VISIBLE_STRING, 129);
    s->typeSpec.structure.elements[1]  = specLeaf("StartTime", MMS_BINARY_TIME, 6);
    s->typeSpec.structure.elements[2]  = specLeaf("Interval", MMS_INTEGER, 32);
    s->typeSpec.structure.elements[3]  = specLeaf("TLE", MMS_INTEGER, 32);
    s->typeSpec.structure.elements[4]  = specLeaf("BufferTime", MMS_INTEGER, 32);
    s->typeSpec.structure.elements[5]  = specLeaf("IntegrityCheck", MMS_INTEGER, 32);
    s->typeSpec.structure.elements[6]  = specLeaf("BlockData", MMS_BOOLEAN, 0);
    s->typeSpec.structure.elements[7]  = specLeaf("Critical", MMS_BOOLEAN, 0);
    s->typeSpec.structure.elements[8]  = specLeaf("RBE", MMS_BOOLEAN, 0);
    s->typeSpec.structure.elements[9]  = specLeaf("AllChangesReported", MMS_BOOLEAN, 0);
    s->typeSpec.structure.elements[10] = specLeaf("Status", MMS_INTEGER, 8);
    s->typeSpec.structure.elements[11] = specLeaf("EventCodeRequested", MMS_INTEGER, 16);
    s->typeSpec.structure.elements[12] = specLeaf("DSConditionsRequested", MMS_INTEGER, 16);
    return s;
}

/* Build the MmsDevice model */

static void
buildModel(void)
{
    g_device = MmsDevice_create(NULL);     /* VMD (VCC) root, unnamed */
    g_domain = MmsDomain_create((char*) g_cfg.domainName); /* ICC domain */

    /* VMD-scope named variables */
    MmsVariableSpecification** vmdVars =
        (MmsVariableSpecification**) calloc(2, sizeof(MmsVariableSpecification*));
    /* TASE2_Version : structure { major: int16, minor: int16 } */
    MmsVariableSpecification* ver = specStruct("TASE2_Version", 2);
    ver->typeSpec.structure.elements[0] = specLeaf("major", MMS_INTEGER, 16);
    ver->typeSpec.structure.elements[1] = specLeaf("minor", MMS_INTEGER, 16);
    vmdVars[0] = ver;
    /* Supported_Features : bitstring of CBB flags */
    vmdVars[1] = specLeaf("Supported_Features", MMS_BIT_STRING, SUPPORTED_FEATURES_BITS);
    g_device->namedVariables = vmdVars;
    g_device->namedVariablesCount = 2;

    /* ICC domain named variables. Sized for the fixed objects (status vars,
     * transfer sets, dev1) plus however many indication points were configured. */
    int idx = 0;
    int cap = 16 + 2 * g_nPoints + MAX_TRANSFER_SETS;  /* each point may add a control object */
    MmsVariableSpecification** d =
        (MmsVariableSpecification**) calloc(cap, sizeof(MmsVariableSpecification*));
    d[idx++] = specLeaf("Bilateral_Table_ID", MMS_VISIBLE_STRING, 64);

    /* Next_DSTransfer_Set : structure where element[2] is the next free TS name
     * (matches what TASE.2 clients read to obtain a transfer-set object name). */
    MmsVariableSpecification* nextTs = specStruct("Next_DSTransfer_Set", 3);
    nextTs->typeSpec.structure.elements[0] = specLeaf("Available", MMS_INTEGER, 16);
    nextTs->typeSpec.structure.elements[1] = specLeaf("Max", MMS_INTEGER, 16);
    nextTs->typeSpec.structure.elements[2] = specLeaf("Name", MMS_VISIBLE_STRING, 32);
    d[idx++] = nextTs;

    /* transfer-set report status variables (read individually + sent in reports) */
    d[idx++] = specLeaf("Transfer_Set_Name", MMS_VISIBLE_STRING, 32);
    d[idx++] = specLeaf("Transfer_Set_Time_Stamp", MMS_BINARY_TIME, 6);
    d[idx++] = specLeaf("DSConditions_Detected", MMS_INTEGER, 16);
    d[idx++] = specLeaf("Event_Code_Detected", MMS_INTEGER, 16);
    d[idx++] = specLeaf("Transfer_Report_ACK", MMS_INTEGER, 16);
    d[idx++] = specLeaf("Transfer_Report_NACK", MMS_INTEGER, 16);

    /* indication points (from the dynamic registry) + a control object each for
     * commandable points */
    for (int i = 0; i < g_nPoints; i++) {
        d[idx++] = g_points[i].isReal ? specRealQ(g_points[i].name)
                                      : specStateQ(g_points[i].name);
        if (g_points[i].control != CTL_NONE)
            d[idx++] = specControl(g_points[i].ctlName,
                                   g_points[i].control == CTL_SETPOINT);
    }

    /* DS Transfer Set objects */
    for (int i = 0; i < MAX_TRANSFER_SETS; i++) {
        char nm[32];
        snprintf(nm, sizeof(nm), "DSTransferSet%02d", i + 1);
        d[idx++] = specTransferSet(nm);
        snprintf(g_transferSets[i].name, sizeof(g_transferSets[i].name), "%s", nm);
        g_transferSets[i].enabled = false;
        g_transferSets[i].dataSetName[0] = '\0';
    }

    /* Block 5: device control point (select-before-operate) */
    MmsVariableSpecification* dev = specStruct("dev1", 3);
    dev->typeSpec.structure.elements[0] = specLeaf("Command", MMS_INTEGER, 8);  /* operate value */
    dev->typeSpec.structure.elements[1] = specLeaf("Tag", MMS_VISIBLE_STRING, 32);
    dev->typeSpec.structure.elements[2] = specLeaf("Status", MMS_INTEGER, 8);
    d[idx++] = dev;

    g_domain->namedVariables = d;          /* sized cap; count below limits use */
    g_domain->namedVariablesCount = idx;

    MmsDomain** domains = (MmsDomain**) calloc(1, sizeof(MmsDomain*));
    domains[0] = g_domain;
    g_device->domains = domains;
    g_device->domainCount = 1;
}

/* Populate the value cache with initial values */

/* { Value, Flags(bitstring 8), TimeStamp(int) }. Flags default 0 = validity VALID,
 * source TELEMETERED. TimeStamp 0 until first update. */
static MmsValue*
makeRealQ(float f)
{
    MmsValue* s = MmsValue_createEmptyStructure(3);
    MmsValue_setElement(s, 0, MmsValue_newFloat(f));
    MmsValue_setElement(s, 1, MmsValue_newBitString(8));
    MmsValue_setElement(s, 2, MmsValue_newIntegerFromInt32(0));
    return s;
}

static MmsValue*
makeStateQ(int st)
{
    MmsValue* s = MmsValue_createEmptyStructure(3);
    MmsValue_setElement(s, 0, MmsValue_newIntegerFromInt32(st));
    MmsValue_setElement(s, 1, MmsValue_newBitString(8));
    MmsValue_setElement(s, 2, MmsValue_newIntegerFromInt32(0));
    return s;
}

static MmsValue*
makeControl(int isSetpoint)
{
    MmsValue* s = MmsValue_createEmptyStructure(4);
    MmsValue_setElement(s, 0, isSetpoint ? MmsValue_newFloat(0.0f)
                                         : MmsValue_newIntegerFromInt32(0));
    MmsValue_setElement(s, 1, MmsValue_newVisibleString(""));
    MmsValue_setElement(s, 2, MmsValue_newIntegerFromInt32(0));
    MmsValue_setElement(s, 3, MmsValue_newIntegerFromInt32(0));
    return s;
}

static void
populateCache(void)
{
    MmsDomain* vmd = (MmsDomain*) MmsServer_getDevice(g_server);

    /* TASE2_Version { major, minor } */
    MmsValue* ver = MmsValue_createEmptyStructure(2);
    MmsValue_setElement(ver, 0, MmsValue_newIntegerFromInt32(TASE2_VERSION_MAJOR));
    MmsValue_setElement(ver, 1, MmsValue_newIntegerFromInt32(TASE2_VERSION_MINOR));
    MmsServer_insertIntoCache(g_server, vmd, "TASE2_Version", ver);

    /* Supported_Features: set bits for Block1(0), Block2(1), Block5(4) */
    MmsValue* feat = MmsValue_newBitString(SUPPORTED_FEATURES_BITS);
    MmsValue_setBitStringBit(feat, 0, true);  /* Block 1 */
    MmsValue_setBitStringBit(feat, 1, true);  /* Block 2 */
    MmsValue_setBitStringBit(feat, 4, true);  /* Block 5 */
    MmsServer_insertIntoCache(g_server, vmd, "Supported_Features", feat);

    /* Bilateral_Table_ID */
    MmsServer_insertIntoCache(g_server, g_domain, "Bilateral_Table_ID",
                              MmsValue_newVisibleString(g_cfg.bltId));

    /* Next_DSTransfer_Set { available, max, name } */
    MmsValue* nextTs = MmsValue_createEmptyStructure(3);
    MmsValue_setElement(nextTs, 0, MmsValue_newIntegerFromInt32(MAX_TRANSFER_SETS));
    MmsValue_setElement(nextTs, 1, MmsValue_newIntegerFromInt32(MAX_TRANSFER_SETS));
    MmsValue_setElement(nextTs, 2, MmsValue_newVisibleString("DSTransferSet01"));
    MmsServer_insertIntoCache(g_server, g_domain, "Next_DSTransfer_Set", nextTs);

    /* transfer-set status vars */
    MmsServer_insertIntoCache(g_server, g_domain, "Transfer_Set_Name",
                              MmsValue_newVisibleString(""));
    g_tsTimeStamp = MmsValue_newBinaryTime(false);
    MmsServer_insertIntoCache(g_server, g_domain, "Transfer_Set_Time_Stamp", g_tsTimeStamp);
    g_dsConditionsDetected = MmsValue_newIntegerFromInt32(0);
    MmsServer_insertIntoCache(g_server, g_domain, "DSConditions_Detected", g_dsConditionsDetected);
    MmsServer_insertIntoCache(g_server, g_domain, "Event_Code_Detected",
                              MmsValue_newIntegerFromInt32(0));
    MmsServer_insertIntoCache(g_server, g_domain, "Transfer_Report_ACK",
                              MmsValue_newIntegerFromInt32(0));
    MmsServer_insertIntoCache(g_server, g_domain, "Transfer_Report_NACK",
                              MmsValue_newIntegerFromInt32(0));

    /* indication points (keep pointers in the registry for live updates) */
    for (int i = 0; i < g_nPoints; i++) {
        g_points[i].cell = g_points[i].isReal ? makeRealQ(0.0f) : makeStateQ(0);
        g_points[i].overrideUntil = 0;
        MmsServer_insertIntoCache(g_server, g_domain, g_points[i].name, g_points[i].cell);
        if (g_points[i].control != CTL_NONE) {
            g_points[i].ctlCell = makeControl(g_points[i].control == CTL_SETPOINT);
            MmsServer_insertIntoCache(g_server, g_domain, g_points[i].ctlName, g_points[i].ctlCell);
        }
    }

    /* DS Transfer Set objects (default values) */
    for (int i = 0; i < MAX_TRANSFER_SETS; i++) {
        MmsVariableSpecification* spec =
            MmsDomain_getNamedVariable(g_domain, g_transferSets[i].name);
        if (spec) {
            MmsValue* tsv = MmsValue_newDefaultValue(spec);
            MmsServer_insertIntoCache(g_server, g_domain, g_transferSets[i].name, tsv);
        }
    }

    /* device control point */
    MmsVariableSpecification* devSpec = MmsDomain_getNamedVariable(g_domain, "dev1");
    if (devSpec)
        MmsServer_insertIntoCache(g_server, g_domain, "dev1", MmsValue_newDefaultValue(devSpec));
}

/* Handlers */

static void
connectionHandler(void* parameter, MmsServerConnection connection,
                  MmsServerEvent event)
{
    if (event == MMS_SERVER_CONNECTION_TICK)
        return;
    char* peer = MmsServerConnection_getClientAddress(connection);
    Semaphore_wait(g_lock);
    if (event == MMS_SERVER_NEW_CONNECTION) {
        LinkedList_add(g_connections, connection);
        printf("[tase2] association from %s\n", peer ? peer : "?");
    } else if (event == MMS_SERVER_CONNECTION_CLOSED) {
        LinkedList_remove(g_connections, connection);
        printf("[tase2] association closed (%s)\n", peer ? peer : "?");
    }
    Semaphore_post(g_lock);
}

/* Peer allowlist: is this association permitted to issue writes/operates? Reads
 * and subscription are always allowed; only the command/injection direction is
 * gated. With no allowlist configured (range default) everyone may write. The
 * client address is "ip" or "ip:port", so match an allowlist entry that is the
 * full address or its ip prefix up to the port separator. */
static int
peerAllowed(MmsServerConnection con)
{
    if (g_cfg.allowCount == 0)
        return 1;
    char* peer = MmsServerConnection_getClientAddress(con);
    if (peer == NULL)
        return 0;
    for (int i = 0; i < g_cfg.allowCount; i++) {
        size_t n = strlen(g_cfg.allow[i]);
        if (strncmp(peer, g_cfg.allow[i], n) == 0 && (peer[n] == ':' || peer[n] == '\0'))
            return 1;
    }
    return 0;
}

/* ---- Bilateral table (per-peer data scoping) ---------------------------- *
 * A TASE.2 bilateral table is the agreement between two control centres that
 * says which data each may see and command. The server publishes its table ID;
 * with -B it also ENFORCES the table: each peer (by IP) gets a rule listing the
 * objects it may read (r), control (c), and write/inject (w). Reads, operates,
 * injections, and report members outside a peer's rule are denied or withheld.
 * With no -B the command path follows the open/-L behaviour as before. */
#define BLT_R 1          /* read and subscribe the listed objects */
#define BLT_C 2          /* control (operate) the listed objects   */
#define BLT_W 4          /* write/inject values to the listed objects */

typedef struct {
    char ip[64];
    int  rights;
    char obj[32][64];
    int  nObj;
    int  all;            /* '*' : every data object */
} BltRule;

static BltRule g_blt[32];
static int     g_nBlt = 0;
static int     g_bltLoaded = 0;

/* Parse a -B file. Lines: "ip rights objects", where rights is any of r/c/w and
 * objects is a comma-separated list of names or "prefix*" patterns, or "*" for
 * all. Blank lines and lines starting with '#' are ignored. */
static void
loadBlt(const char* path)
{
    FILE* f = fopen(path, "r");
    if (f == NULL) {
        fprintf(stderr, "[tase2] cannot open bilateral table %s\n", path);
        exit(1);
    }
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        char ip[64], rights[16], objs[400];
        int got = sscanf(line, " %63s %15s %399s", ip, rights, objs);
        if (got < 2 || ip[0] == '#')
            continue;
        if (g_nBlt >= 32) { fprintf(stderr, "[tase2] too many BLT rules\n"); exit(1); }
        BltRule* r = &g_blt[g_nBlt++];
        snprintf(r->ip, sizeof(r->ip), "%s", ip);
        r->rights = 0;
        for (char* p = rights; *p; p++) {
            if (*p == 'r') r->rights |= BLT_R;
            else if (*p == 'c') r->rights |= BLT_C;
            else if (*p == 'w') r->rights |= BLT_W;
        }
        r->nObj = 0;
        r->all = 0;
        if (got >= 3) {
            if (!strcmp(objs, "*")) r->all = 1;
            else for (char* tok = strtok(objs, ","); tok && r->nObj < 32;
                      tok = strtok(NULL, ","))
                snprintf(r->obj[r->nObj++], sizeof(r->obj[0]), "%s", tok);
        }
    }
    fclose(f);
    g_bltLoaded = 1;
}

/* The BLT object key for an item: drop a "$Member" suffix and a trailing "_ctl",
 * so a rule listing "plc1_brk" covers its value and its control object. */
static void
bltBaseName(const char* in, char* out, size_t n)
{
    snprintf(out, n, "%s", in);
    char* d = strchr(out, '$');
    if (d) *d = '\0';
    size_t l = strlen(out);
    if (l > 4 && !strcmp(out + l - 4, "_ctl")) out[l - 4] = '\0';
}

static BltRule*
bltRuleFor(MmsServerConnection con)
{
    char* peer = MmsServerConnection_getClientAddress(con);
    if (peer == NULL) return NULL;
    char ip[64];
    size_t i = 0;
    for (; peer[i] && peer[i] != ':' && i + 1 < sizeof(ip); i++) ip[i] = peer[i];
    ip[i] = '\0';
    for (int j = 0; j < g_nBlt; j++)
        if (!strcmp(g_blt[j].ip, ip)) return &g_blt[j];
    return NULL;
}

static int
objMatches(BltRule* r, const char* name)
{
    if (r->all) return 1;
    for (int i = 0; i < r->nObj; i++) {
        size_t plen = strlen(r->obj[i]);
        if (plen > 0 && r->obj[i][plen - 1] == '*') {
            if (strncmp(name, r->obj[i], plen - 1) == 0) return 1;
        } else if (strcmp(name, r->obj[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Metadata/handshake objects every peer may always read, so association and
 * discovery work; only the data objects are scoped by the table. */
static int
isHandshakeObject(const char* base)
{
    static const char* meta[] = {
        "TASE2_Version", "Supported_Features", "Bilateral_Table_ID",
        "Next_DSTransfer_Set", "Transfer_Set_Name", "Transfer_Set_Time_Stamp",
        "DSConditions_Detected", "Event_Code_Detected",
        "Transfer_Report_ACK", "Transfer_Report_NACK",
    };
    for (size_t i = 0; i < sizeof(meta) / sizeof(meta[0]); i++)
        if (!strcmp(base, meta[i])) return 1;
    return !strncmp(base, "DSTransferSet", 13);   /* subscription config */
}

/* Does this connection hold 'right' on the (already base-keyed) object? With no
 * table loaded, everything is permitted (the open default). An unknown peer with
 * a table loaded is denied (default deny). */
static int
bltAllows(MmsServerConnection con, const char* base, int right)
{
    if (!g_bltLoaded) return 1;
    BltRule* r = bltRuleFor(con);
    if (r == NULL) return 0;
    return (r->rights & right) && objMatches(r, base);
}

/* A withheld report member: same shape, but value zeroed and quality NOT-VALID,
 * so a scoped peer sees the point exists without its data. */
static MmsValue*
withheldClone(MmsValue* v)
{
    MmsValue* c = MmsValue_clone(v);
    if (MmsValue_getType(c) == MMS_STRUCTURE && MmsValue_getArraySize(c) >= 2) {
        MmsValue* val = MmsValue_getElement(c, 0);
        if (val && MmsValue_getType(val) == MMS_FLOAT)        MmsValue_setFloat(val, 0.0f);
        else if (val && MmsValue_getType(val) == MMS_INTEGER) MmsValue_setInt32(val, 0);
        MmsValue* q = MmsValue_getElement(c, 1);
        if (q && MmsValue_getType(q) == MMS_BIT_STRING)
            MmsValue_setBitStringFromInteger(q, 12);          /* validity NOTVALID */
    }
    return c;
}

/* Read-access gate: deny a peer reading a data object outside its bilateral
 * table. Handshake objects and the unscoped (no -B) case are always allowed. */
static MmsDataAccessError
readAccessHandler(void* parameter, MmsDomain* domain, char* variableId,
                  MmsServerConnection connection, bool isDirectAccess)
{
    (void) parameter; (void) domain; (void) isDirectAccess;
    if (!g_bltLoaded) return DATA_ACCESS_ERROR_SUCCESS;
    char base[64];
    bltBaseName(variableId, base, sizeof(base));
    if (isHandshakeObject(base)) return DATA_ACCESS_ERROR_SUCCESS;
    if (bltAllows(connection, base, BLT_R)) return DATA_ACCESS_ERROR_SUCCESS;
    return DATA_ACCESS_ERROR_OBJECT_ACCESS_DENIED;
}

/* Write handler: device control (Block 5) and transfer-set enable (Block 2).
 * domain==NULL means VMD scope.
 *
 * Clients may address a structure member either as a component write
 * (componentId set) or as a flattened "Base$Member" itemId. We normalise both
 * into baseName + member. */
static MmsDataAccessError
writeHandler(void* parameter, MmsDomain* domain, const char* variableId,
             int arrayIdx, const char* componentId, MmsValue* value,
             MmsServerConnection connection)
{
    char baseName[64];
    const char* member = componentId;

    snprintf(baseName, sizeof(baseName), "%s", variableId);
    if (member == NULL) {
        char* dollar = strchr(baseName, '$');
        if (dollar) { *dollar = '\0'; member = dollar + 1; }
    }

    /* Command/injection writes (device control, indication-point values) are
     * gated by the peer allowlist. Transfer-set configuration (Block 2
     * subscription) is exempt so any peer may still subscribe and read. */
    if (strncmp(baseName, "DSTransferSet", 13) != 0) {
        if (!peerAllowed(connection)) {
            printf("[tase2] write to %s denied: peer not in allowlist\n", baseName);
            return DATA_ACCESS_ERROR_OBJECT_ACCESS_DENIED;
        }
        if (g_bltLoaded) {
            /* control objects need 'c'; indication-point value writes need 'w' */
            size_t bl = strlen(baseName);
            int right = (bl > 4 && !strcmp(baseName + bl - 4, "_ctl")) ? BLT_C : BLT_W;
            char bkey[64];
            bltBaseName(baseName, bkey, sizeof(bkey));
            if (!bltAllows(connection, bkey, right)) {
                printf("[tase2] write to %s denied by bilateral table\n", baseName);
                return DATA_ACCESS_ERROR_OBJECT_ACCESS_DENIED;
            }
        }
    }

    /* Block 5 device control: client operates dev1 -> log + accept */
    if (strcmp(baseName, "dev1") == 0) {
        printf("[tase2] device control operate on dev1.%s\n",
               member ? member : "(whole)");
        return DATA_ACCESS_ERROR_SUCCESS;
    }

    /* Block 5 device control on a per-point control object <name>_ctl. The
     * operated Command is stored in the cache so the ingestion gateway can read it
     * and push it down to the PLC (the southbound command path). Select-before-
     * operate devices enforce select -> operate ownership and a select timeout. */
    for (int i = 0; i < g_nPoints; i++) {
        if (g_points[i].control == CTL_NONE || strcmp(baseName, g_points[i].ctlName) != 0)
            continue;
        Point* p = &g_points[i];
        MmsValue* cell = p->ctlCell;
        uint64_t now = Hal_getTimeInMs();

        if (cell && member && strcmp(member, "SBO") == 0) {
            /* Select request. Grant unless another connection holds an unexpired
             * selection. */
            if (p->ctlSelBy && p->ctlSelBy != connection && now < p->ctlSelExpiry) {
                printf("[tase2] %s select denied (held by another)\n", p->ctlName);
                return DATA_ACCESS_ERROR_OBJECT_ACCESS_DENIED;
            }
            int sel = (MmsValue_getType(value) == MMS_INTEGER) ? MmsValue_toInt32(value) : 1;
            if (sel) {
                p->ctlSelBy = connection;
                p->ctlSelExpiry = now + SBO_SELECT_TIMEOUT_MS;
                printf("[tase2] %s selected\n", p->ctlName);
            } else {
                p->ctlSelBy = NULL;     /* deselect / cancel */
            }
            MmsValue* sb = MmsValue_getElement(cell, 3);
            if (sb) MmsValue_setInt32(sb, sel ? 1 : 0);
            return DATA_ACCESS_ERROR_SUCCESS;
        }

        if (cell && member && strcmp(member, "Command") == 0) {
            if (p->ctlSbo) {
                /* operate is only valid for the selecting connection, unexpired */
                if (p->ctlSelBy != connection || now >= p->ctlSelExpiry) {
                    printf("[tase2] %s operate rejected (not selected)\n", p->ctlName);
                    return DATA_ACCESS_ERROR_OBJECT_ACCESS_DENIED;
                }
                p->ctlSelBy = NULL;     /* one operate per select */
                MmsValue* sb = MmsValue_getElement(cell, 3);
                if (sb) MmsValue_setInt32(sb, 0);
            }
            MmsValue* el = MmsValue_getElement(cell, 0);
            if (el) {
                if (MmsValue_getType(el) == MMS_FLOAT) MmsValue_setFloat(el, MmsValue_toFloat(value));
                else                                   MmsValue_setInt32(el, MmsValue_toInt32(value));
            }
            MmsValue* st = MmsValue_getElement(cell, 2);
            if (st) MmsValue_setInt32(st, 1);   /* Status: command operated */
            printf("[tase2] control %s commanded%s\n", p->ctlName,
                   p->ctlSbo ? " (SBO)" : "");
        } else if (cell && member && strcmp(member, "Tag") == 0 &&
                   MmsValue_getType(value) == MMS_VISIBLE_STRING) {
            MmsValue* el = MmsValue_getElement(cell, 1);
            if (el) MmsValue_setVisibleString(el, (char*) MmsValue_toString(value));
        }
        return DATA_ACCESS_ERROR_SUCCESS;
    }

    /* Indication point writes from the ingestion gateway (or a false-data
     * injector): Value, Flags (the TASE.2 quality byte), and TimeStamp. Value
     * writes are held for the injection window so they reach the BA in reports
     * before the simulation (if any) resumes. Flags and TimeStamp carry the
     * point's quality and acquisition time end to end from the field. */
    {
        int pi = pointIndex(baseName);
        if (pi >= 0) {
            MmsValue* cell = g_points[pi].cell;
            if (cell && member && strcmp(member, "Flags") == 0) {
                MmsValue* q = MmsValue_getElement(cell, 1);
                if (q && MmsValue_getType(q) == MMS_BIT_STRING) {
                    uint32_t qv = (MmsValue_getType(value) == MMS_BIT_STRING)
                        ? MmsValue_getBitStringAsInteger(value)
                        : (uint32_t) MmsValue_toInt32(value);
                    MmsValue_setBitStringFromInteger(q, qv);
                }
                return DATA_ACCESS_ERROR_SUCCESS;
            }
            if (cell && member && strcmp(member, "TimeStamp") == 0) {
                MmsValue* t = MmsValue_getElement(cell, 2);
                if (t) MmsValue_setInt32(t, MmsValue_toInt32(value));
                return DATA_ACCESS_ERROR_SUCCESS;
            }
            if (member == NULL || strcmp(member, "Value") == 0) {
                MmsValue* el = MmsValue_getElement(cell, 0);
                if (el) {
                    if (MmsValue_getType(el) == MMS_FLOAT)
                        MmsValue_setFloat(el, MmsValue_toFloat(value));
                    else if (MmsValue_getType(el) == MMS_INTEGER)
                        MmsValue_setInt32(el, MmsValue_toInt32(value));
                }
                if (g_cfg.overrideHoldSeconds > 0)
                    g_points[pi].overrideUntil =
                        Hal_getTimeInMs() + (uint64_t) g_cfg.overrideHoldSeconds * 1000;
                return DATA_ACCESS_ERROR_SUCCESS;
            }
        }
    }

    /* Block 2 transfer-set configuration: client writes DSTransferSetNN
     * attributes to bind a data set and enable reporting. */
    if (strncmp(baseName, "DSTransferSet", 13) == 0) {
        Semaphore_wait(g_lock);
        for (int i = 0; i < MAX_TRANSFER_SETS; i++) {
            if (strcmp(baseName, g_transferSets[i].name) != 0)
                continue;
            /* Mirror the written attribute into the cache as well, so a client
             * that writes a transfer-set attribute and reads it back sees the
             * value it set (write/read consistency a conformance client expects).
             * Element order matches specTransferSet(). */
            MmsValue* tsv = MmsServer_getValueFromCache(g_server, g_domain, baseName);
            if (member && strcmp(member, "DataSetName") == 0 &&
                MmsValue_getType(value) == MMS_VISIBLE_STRING) {
                snprintf(g_transferSets[i].dataSetName,
                         sizeof(g_transferSets[i].dataSetName), "%s",
                         MmsValue_toString(value));
                if (tsv) MmsValue_setVisibleString(MmsValue_getElement(tsv, 0),
                                                   (char*) MmsValue_toString(value));
                printf("[tase2] %s bound to data set '%s'\n",
                       g_transferSets[i].name, g_transferSets[i].dataSetName);
            } else if (member && strcmp(member, "Status") == 0) {
                g_transferSets[i].enabled = (MmsValue_toInt32(value) != 0);
                if (tsv) MmsValue_setInt32(MmsValue_getElement(tsv, 10), MmsValue_toInt32(value));
                printf("[tase2] %s %s\n", g_transferSets[i].name,
                       g_transferSets[i].enabled ? "ENABLED" : "disabled");
            } else if (member && strcmp(member, "Interval") == 0) {
                g_transferSets[i].interval = MmsValue_toInt32(value);
                if (tsv) MmsValue_setInt32(MmsValue_getElement(tsv, 2), MmsValue_toInt32(value));
            } else if (member && strcmp(member, "DSConditionsRequested") == 0) {
                g_transferSets[i].dsConditions = MmsValue_toInt32(value);
                if (tsv) MmsValue_setInt32(MmsValue_getElement(tsv, 12), MmsValue_toInt32(value));
            }
            break;
        }
        Semaphore_post(g_lock);
        return DATA_ACCESS_ERROR_SUCCESS;
    }

    return DATA_ACCESS_ERROR_SUCCESS; /* accept other writes */
}

/* reporting: send an unconfirmed InformationReport for each enabled transfer set */

static void
sendTransferSetReport(MmsServerConnection con, TransferSet* ts, int conditions)
{
    LinkedList values = LinkedList_create();

    /* Report header: Transfer_Set_Name, Time_Stamp, DSConditions_Detected */
    LinkedList_add(values, MmsValue_newVisibleString(ts->name));
    MmsValue* tstamp = MmsValue_newBinaryTime(false);
    MmsValue_setBinaryTime(tstamp, Hal_getTimeInMs());
    LinkedList_add(values, tstamp);
    LinkedList_add(values, MmsValue_newIntegerFromInt32(conditions));

    /* Data set member values: walk the named variable list (data set) the
     * client created and clone each member's current value from the cache. */
    if (ts->dataSetName[0] != '\0') {
        MmsNamedVariableList nvl =
            MmsDomain_getNamedVariableList(g_domain, ts->dataSetName);
        if (nvl) {
            LinkedList entries = MmsNamedVariableList_getVariableList(nvl);
            LinkedList e = LinkedList_getNext(entries);
            while (e) {
                MmsNamedVariableListEntry entry = (MmsNamedVariableListEntry) e->data;
                MmsDomain* dom = MmsNamedVariableListEntry_getDomain(entry);
                char* nm = MmsNamedVariableListEntry_getVariableName(entry);
                MmsValue* v = MmsServer_getValueFromCache(g_server, dom, nm);
                if (v) {
                    /* scope report members to what this peer may read; others are
                     * sent withheld (zeroed, NOT-VALID) so the table is enforced
                     * on Block 2 reports too, not just direct reads */
                    char bkey[64];
                    bltBaseName(nm, bkey, sizeof(bkey));
                    if (isHandshakeObject(bkey) || bltAllows(con, bkey, BLT_R))
                        LinkedList_add(values, MmsValue_clone(v));
                    else
                        LinkedList_add(values, withheldClone(v));
                }
                e = LinkedList_getNext(e);
            }
        }
    }

    /* Unconfirmed PDU / InformationReport, VMD-specific itemId = transfer set */
    MmsServerConnection_sendInformationReportVMDSpecific(con, ts->name, values, false);
    LinkedList_destroyDeep(values, (LinkedListValueDeleteFunction) MmsValue_delete);
    ts->reportsSent++;
}

/* periodic work, run from the main loop */

static void
simulateValues(void)
{
    static double t = 0.0;
    t += 1.0;
    uint64_t now = Hal_getTimeInMs();

    /* expire any injection override whose hold window has passed */
    for (int i = 0; i < g_nPoints; i++) {
        if (g_points[i].overrideUntil && now >= g_points[i].overrideUntil) {
            g_points[i].overrideUntil = 0;
            printf("[tase2] %s override expired; simulation resumed\n", g_points[i].name);
        }
    }

    /* Synthetic value source for any point not currently held by an injected
     * value. Each point gets a phase offset by its index so a wall of points
     * does not move in lockstep. Real points trace a sine; state points toggle. */
    MmsServer_lockModel(g_server);
    for (int i = 0; i < g_nPoints; i++) {
        if (g_points[i].overrideUntil || g_points[i].cell == NULL) continue;
        MmsValue* el = MmsValue_getElement(g_points[i].cell, 0);
        if (el == NULL) continue;
        if (g_points[i].isReal)
            MmsValue_setFloat(el, (float)(11.0 + 5.0 * sin((t + i * 3.0) / 5.0)));
        else
            MmsValue_setInt32(el, ((int)(t / (i + 1)) % 2));
    }
    MmsServer_unlockModel(g_server);
}

static void
reportingTick(int integrityDue)
{
    Semaphore_wait(g_lock);
    for (int i = 0; i < MAX_TRANSFER_SETS; i++) {
        TransferSet* ts = &g_transferSets[i];
        if (!ts->enabled) continue;
        int cond = integrityDue ? DSCOND_INTEGRITY : DSCOND_OBJECT_CHANGE;

        LinkedList c = LinkedList_getNext(g_connections);
        MmsServer_lockModel(g_server);
        while (c) {
            sendTransferSetReport((MmsServerConnection) c->data, ts, cond);
            c = LinkedList_getNext(c);
        }
        MmsServer_unlockModel(g_server);
    }
    Semaphore_post(g_lock);
}

/* main */

static void sigHandler(int sig) { (void)sig; g_running = 0; }

static void
parseArgs(int argc, char** argv)
{
    g_cfg.bindIp = NULL;
    g_cfg.port = TASE2_DEFAULT_PORT;
    g_cfg.domainName = TASE2_DEFAULT_DOMAIN;
    g_cfg.bltId = TASE2_DEFAULT_BLT_ID;
    g_cfg.integritySeconds = 30;
    g_cfg.tls = 0;
    g_cfg.certFile = NULL;
    g_cfg.keyFile = NULL;
    g_cfg.caFile = NULL;
    g_cfg.overrideHoldSeconds = 30;
    g_cfg.pointsFile = NULL;
    g_cfg.noSim = 0;
    g_cfg.allowCount = 0;
    g_cfg.bltFile = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-i") && i + 1 < argc) g_cfg.bindIp = argv[++i];
        else if (!strcmp(argv[i], "-p") && i + 1 < argc) g_cfg.port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-d") && i + 1 < argc) g_cfg.domainName = argv[++i];
        else if (!strcmp(argv[i], "-b") && i + 1 < argc) g_cfg.bltId = argv[++i];
        else if (!strcmp(argv[i], "-t") && i + 1 < argc) g_cfg.integritySeconds = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-T")) g_cfg.tls = 1;
        else if (!strcmp(argv[i], "-C") && i + 1 < argc) g_cfg.certFile = argv[++i];
        else if (!strcmp(argv[i], "-K") && i + 1 < argc) g_cfg.keyFile = argv[++i];
        else if (!strcmp(argv[i], "-A") && i + 1 < argc) g_cfg.caFile = argv[++i];
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) g_cfg.overrideHoldSeconds = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-P") && i + 1 < argc) g_cfg.pointsFile = argv[++i];
        else if (!strcmp(argv[i], "-B") && i + 1 < argc) g_cfg.bltFile = argv[++i];
        else if (!strcmp(argv[i], "-n")) g_cfg.noSim = 1;
        else if (!strcmp(argv[i], "-L") && i + 1 < argc) {
            char* list = strdup(argv[++i]);
            for (char* tok = strtok(list, ","); tok && g_cfg.allowCount < 16;
                 tok = strtok(NULL, ","))
                g_cfg.allow[g_cfg.allowCount++] = tok;   /* points into the strdup'd copy */
        }
        else if (!strcmp(argv[i], "-h")) {
            printf("usage: %s [-i bindIp] [-p port] [-d domain] [-b bltId] [-t integritySecs]\n"
                   "          [-o injectHoldSecs] [-P pointsFile] [-n] [-L allowIp[,ip...]]\n"
                   "          [-B bilateralTable] [-T] [-C serverCert.pem] [-K serverKey.pem] [-A caCert.pem]\n", argv[0]);
            exit(0);
        }
    }
}

int
main(int argc, char** argv)
{
    parseArgs(argc, argv);
    signal(SIGINT, sigHandler);
    signal(SIGTERM, sigHandler);

    g_lock = Semaphore_create(1);
    g_connections = LinkedList_create();

    loadPoints();
    buildModel();
    if (g_cfg.bltFile) loadBlt(g_cfg.bltFile);

    TLSConfiguration tlsConfig = NULL;
    if (g_cfg.tls) {
        tlsConfig = TLSConfiguration_create();
        if (g_cfg.certFile) TLSConfiguration_setOwnCertificateFromFile(tlsConfig, g_cfg.certFile);
        if (g_cfg.keyFile)  TLSConfiguration_setOwnKeyFromFile(tlsConfig, g_cfg.keyFile, NULL);
        if (g_cfg.caFile)   TLSConfiguration_addCACertificateFromFile(tlsConfig, g_cfg.caFile);
        /* if a CA is given, require + validate client certs (mutual TLS) */
        TLSConfiguration_setChainValidation(tlsConfig, g_cfg.caFile ? true : false);
        TLSConfiguration_setAllowOnlyKnownCertificates(tlsConfig, false);
        printf("[tase2] TLS / Secure ICCP enabled\n");
    }

    g_server = MmsServer_create(g_device, tlsConfig);
    MmsServer_setMaxConnections(g_server, 10);
    MmsServer_enableDynamicNamedVariableListService(g_server, true);
    MmsServer_setMaxDomainSpecificDataSets(g_server, 32);
    MmsServer_setMaxDataSetEntries(g_server, 64);
    MmsServer_setServerIdentity(g_server, "FreeTASE2", "tase2-server-sim", "0.1");
    MmsServer_installWriteHandler(g_server, writeHandler, NULL);
    MmsServer_installConnectionHandler(g_server, connectionHandler, NULL);
    MmsServer_installReadAccessHandler(g_server, readAccessHandler, NULL);

    populateCache();

    if (g_cfg.bindIp)
        MmsServer_setLocalIpAddress(g_server, g_cfg.bindIp);

    printf("[tase2] TASE.2/ICCP server starting: domain=%s blt=%s port=%d\n",
           g_cfg.domainName, g_cfg.bltId, g_cfg.port);
    printf("[tase2] VMD: TASE2_Version=%d-%d, Supported_Features=Block1,2,5\n",
           TASE2_VERSION_MAJOR, TASE2_VERSION_MINOR);
    printf("[tase2] publishing %d indication point(s)%s; value source: %s\n", g_nPoints,
           g_cfg.pointsFile ? " from points file" : " (built-in tm/ts set)",
           g_cfg.noSim ? "external writes only (no simulation)" : "internal simulation");
    if (g_cfg.allowCount > 0)
        printf("[tase2] command allowlist: %d peer(s) may write/operate\n", g_cfg.allowCount);
    else
        printf("[tase2] command allowlist: OPEN (any peer may write/operate)\n");
    if (g_bltLoaded)
        printf("[tase2] bilateral table ENFORCED: %d peer rule(s); reads, controls, and report members are scoped\n", g_nBlt);
    else
        printf("[tase2] bilateral table: published only (not enforced; use -B to enforce)\n");

    /* libIEC61850 here is built single-threaded (CONFIG_MMS_SINGLE_THREADED=1),
     * so we drive the MMS stack and our periodic work from one loop. */
    MmsServer_startListeningThreadless(g_server, g_cfg.port);

    int tick = 0;
    uint64_t lastTick = Hal_getTimeInMs();
    while (g_running) {
        MmsServer_waitReady(g_server, 100);
        MmsServer_handleIncomingMessages(g_server);
        MmsServer_handleBackgroundTasks(g_server);

        uint64_t now = Hal_getTimeInMs();
        if (now - lastTick >= 1000) {
            lastTick = now;
            if (!g_cfg.noSim) simulateValues();
            tick++;
            int integrityDue = (tick % g_cfg.integritySeconds) == 0;
            reportingTick(integrityDue);
        }
    }

    printf("\n[tase2] shutting down\n");
    MmsServer_destroy(g_server);
    Semaphore_destroy(g_lock);
    return 0;
}
