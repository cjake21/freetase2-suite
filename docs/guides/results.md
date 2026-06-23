# Reading results

## In the HMI

Each station card shows its points with value and unit, a quality tag (GOOD,
SUSPECT, HELD, NOTVALID, or STALE), and a station ONLINE or OFFLINE badge derived
from point quality. The header shows how many stations are online. The alarm strip
and event log show high-value and comms-loss events and operator actions.

## Over the API

`GET /api/state` returns the same view as JSON. Per point you get `value`,
`quality`, `ts` (field time tag, Unix seconds), `age` (seconds since `ts`), and
`fresh`. See {doc}`../api/rest`.

`GET /api/events` is a Server-Sent Events stream that pushes the full state on every
change, which is how the HMI stays live.

## Over ICCP

Any TASE.2 client can read the points directly or subscribe to a transfer set and
receive Block 2 reports carrying value, quality, and time. This is the
interoperable path that a real control center or a test client uses. See
{doc}`../developer/testing` for the interoperability test that does exactly this.

## Interpreting quality

| Quality | Meaning |
|---------|---------|
| GOOD / VALID | a recent, valid field read |
| NOTVALID | the device read failed; the last value is held |
| HELD / SUSPECT | set by a source that marks the value held or suspect |
| STALE | no recent report; the link may be down |
