# Error handling

## HMI HTTP API

The control API returns JSON and never crashes on malformed input. Status codes:

| Status | Meaning | Body |
|--------|---------|------|
| `200` | success | `{"ok": true}` or the requested data |
| `400` | bad request (malformed JSON, wrong types, unknown action, point not controllable, SBO not selected) | `{"error": "..."}` |
| `404` | unknown path | `{"error": "not found"}` |
| `413` | request body too large (cap is 64 KB) | `{"error": "request too large"}` |
| `500` | internal error (guarded; should not occur) | `{"error": "internal error: ..."}` |

The body cap and the strict validation are deliberate: the endpoint is a fuzz target
in security testbeds, so it is built to reject rather than fall over. See the fuzz
tests in {doc}`../developer/testing`.

## TASE.2 / ICCP write errors

Writes and operates over ICCP return an MMS data-access result. The common one to
know is object-access-denied, returned when:

- a select-before-operate point is operated without a current selection, or
- the peer is not on the command allowlist in the `hardened` profile.

The ICCP agent surfaces the result code in its `write` and `operate` events
(`"err": N`), which the bridge and tooling can inspect.

## Field-side errors

A failed device read marks the point NOTVALID (the last value is held) rather than
raising to the operator. Connection-based drivers drop and reconnect on the next
poll. One bad tag never stops the others.
