# Basic workflow

The typical path from nothing to a running testbed node:

1. **Define the published points** in `config/scada.json`: the stations and the
   points each one exposes over ICCP. See {doc}`../guides/configuration`.
2. **Map points to the field** in `ingest/tags.json`: which device register or DNP3
   index each point reads, and where commands are written. The point `name` is the
   join key between the two files.
3. **Validate** the configuration:
   ```bash
   python3 scripts/validate_config.py config/scada.json ingest/tags.json
   ```
4. **Run** the stack:
   ```bash
   TAGS=ingest/tags.json ./scripts/55_run_scada.sh
   ```
5. **Observe** in the HMI at `http://127.0.0.1:8800`, or read the state API at
   `GET /api/state`. See {doc}`output` and {doc}`../api/index`.
6. **Command** a controllable point from the HMI, or via `POST /api/control`.

```{note}
Editing `ingest/tags.json` while the gateway runs reloads it on the next poll. The
point model in `config/scada.json` is read at startup, so adding a station means
restarting the stack.
```
