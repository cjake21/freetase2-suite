# Adding custom modules

A driver is a small class with a `read()` method, an optional `write_control()` for
controllable points, and a `close()`. Register it in `DRIVERS`.

## Interface

```python
class MyReader:
    def __init__(self, tag):
        # tag is the resolved tag dict (device fields merged in)
        self.addr = tag["address"]

    def read(self):
        # return a number; raise OSError/ValueError on a bad read so the gateway
        # marks the point NOTVALID and (for connection-based drivers) reconnects
        return self._read_value()

    def write_control(self, control, value):
        # optional: push a command down to the device
        self._write(control["target"], value)

    def close(self):
        pass
```

## Register it

```python
DRIVERS = {
    "stub": StubReader,
    "modbus": ModbusTcpReader,
    "dnp3": Dnp3Reader,
    "myproto": MyReader,      # add here
}
```

Then use `"driver": "myproto"` in a tag.

## Guidelines

- Parse peer-controlled bytes defensively. Bounds-check, cap counts and loops, and
  raise `ValueError` (not `IndexError` or `struct.error`) on malformed input. Add a
  case to the fuzz suite. See {doc}`../developer/testing`.
- Pool connections per device if the protocol is connection based.
- Keep it standard library if you can; the gateway has no third-party dependencies.
- For control, treat the command object and the read-back point as separate, the
  same way the built-in drivers do.

The next page is a complete worked example.
