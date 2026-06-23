# Terminology

```{glossary}
TASE.2 / ICCP
  Telecontrol Application Service Element 2, IEC 60870-6. The inter-control-center
  protocol carried over MMS (ISO 9506). There is no separate TASE.2 PDU on the
  wire; what makes a capture TASE.2 is the object model and the transfer-set and
  report behaviour layered on MMS.

MMS
  Manufacturing Message Specification, ISO 9506. The application protocol TASE.2
  runs on. The server here is built on the libIEC61850 MMS engine.

VMD scope
  Virtual Manufacturing Device scope. Objects read with a null domain, such as
  `TASE2_Version` and `Supported_Features`.

ICC domain
  The Inter-Control-Center domain that holds the bilateral table, transfer sets,
  indication points, and control objects. Its name is the `domain` in
  `config/scada.json`.

Indication point
  A published data value. A `real` point carries a float, a `state` point carries
  an integer, each with a quality byte and a time tag.

Quality byte
  The TASE.2 flags byte (IEC 60870-6-802 style): validity in bits 2 and 3, current
  source in bits 4 and 5, normal value in bit 6, time stamp quality in bit 7.

Transfer set
  A Block 2 object (`DSTransferSetNN`) that binds a data set and controls reporting
  by exception and integrity.

Block 2
  Report-by-exception and integrity reporting of transfer sets, sent as unconfirmed
  MMS InformationReport PDUs.

Block 5
  Device control. A control object with Command, Tag, Status, and SBO members.

Direct operate
  A control where a single operate command takes effect immediately.

Select-before-operate (SBO)
  A control where the client must select the device, then operate within a timeout,
  and only the selecting connection may operate.

CROB
  Control Relay Output Block, DNP3 group 12. The standard DNP3 binary control.

Gateway
  The southbound ingestion process (`tase2_ingest`). A Modbus and DNP3 master that
  polls devices up and writes commands down.

Station
  One device or feed. The HMI renders one card per station.
```
