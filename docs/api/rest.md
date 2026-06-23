# REST API endpoints

Base URL is the HMI bridge, by default `http://127.0.0.1:8800`.

## `GET /api/state`

Returns the full HMI state as JSON.

Top-level fields:

| Field | Type | Meaning |
|-------|------|---------|
| `server` | object | `host`, `port`, `domain` of the TASE.2 server |
| `online` | object | `A` (writer) and `B` (subscriber) association status |
| `meta` | object | protocol metadata (version, features, bilateral table, transfer set) |
| `report` | object | `last_report_time`, `count`, `cond` |
| `stations` | array | one entry per station |

Each station: `id`, `name`, `online`, and `points`. Each point:

| Field | Meaning |
|-------|---------|
| `name`, `label`, `unit`, `type` | identity and display |
| `value` | current value |
| `quality` | `VALID`, `SUSPECT`, `HELD`, or `NOTVALID` |
| `ts`, `age` | field time tag (Unix seconds) and seconds since |
| `fresh` | true when valid and recent |
| `control` | `null`, `discrete`, or `setpoint` |
| `mode` | `direct` or `sbo` for a controllable point |
| `armed` | seconds left on an SBO selection, else 0 |
| `states` | integer to text map for a state point |

## `GET /api/events`

A `text/event-stream` (Server-Sent Events) feed. The server pushes the full state
object as a `data:` line on every change, and a keepalive comment when idle. This is
how the HMI stays live.

## `POST /api/control`

Issue an operator action. Body is a JSON object with an `action`.

| Action | Body | Effect |
|--------|------|--------|
| `command` | `{"action":"command","item":"<point>","value":<n>}` | operate or setpoint the point |
| `select` | `{"action":"select","item":"<point>"}` | SBO select (arm) |
| `cancel` | `{"action":"cancel","item":"<point>"}` | SBO cancel |
| `snapshot` | `{"action":"snapshot"}` | refresh protocol metadata |

Success returns `{"ok": true}`. Errors return a JSON `{"error": "..."}` with a 4xx
status. See {doc}`errors`.

## `GET /` and `GET /static/...`

Serve the HMI page and its assets.
