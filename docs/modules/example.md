# Example module

A complete driver that reads a value over HTTP (for an IoT-style device that
exposes a JSON endpoint) and writes a command with an HTTP POST. Standard library
only.

```python
import json
import urllib.request


class HttpJsonReader:
    """Reads tag['field'] from a JSON endpoint, and (optionally) POSTs commands.

    Tag fields:
      url     : GET endpoint returning a JSON object
      field   : key to read from the JSON
      cmd_url : (control) POST endpoint
    """

    def __init__(self, tag):
        self.url = tag["url"]
        self.field = tag.get("field", "value")
        self.cmd_url = tag.get("cmd_url")
        self.timeout = float(tag.get("timeout", 3))

    def read(self):
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as r:
                doc = json.loads(r.read())
        except (OSError, ValueError) as e:
            raise OSError("http read failed: %s" % e)
        if self.field not in doc:
            raise ValueError("field %r not in response" % self.field)
        return doc[self.field]

    def write_control(self, control, value):
        url = control.get("url", self.cmd_url)
        if not url:
            raise ValueError("no command url configured")
        body = json.dumps({"value": value}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=self.timeout).read()

    def close(self):
        pass
```

Register it:

```python
DRIVERS["http"] = HttpJsonReader
```

Use it in a tag:

```json
{ "point": "plc9_mw", "type": "float", "driver": "http",
  "url": "http://10.50.0.9/meter", "field": "mw",
  "control": { "url": "http://10.50.0.9/breaker" } }
```

Notes:

- `read()` raises `OSError`/`ValueError` on failure, so the gateway marks the point
  NOTVALID and keeps polling the others.
- The control writes to a separate endpoint, mirroring how the command object and
  the monitoring point are kept separate.
