# Authentication

## HMI HTTP API

The HMI bridge HTTP API has no authentication. It is intended to be bound to
loopback or to a trusted management segment, not exposed to untrusted networks.

```{warning}
Do not expose `/api/control` to an untrusted network. Anyone who can reach it can
issue commands. Bind the HMI to loopback (the default `HTTP_HOST=127.0.0.1`) or put
it behind an authenticating reverse proxy and a firewall.
```

The container image binds the HMI to `0.0.0.0` so a published port works; restrict
access at the Docker network or host firewall.

## TASE.2 / ICCP boundary

Authentication and confidentiality on the ICCP boundary use TLS, not the HTTP API.

- The `hardened` profile runs the server with mutual TLS (Secure ICCP). Peers must
  present a certificate that validates against the configured CA.
- The command allowlist (`-L`) restricts which peers may write or operate, by source
  IP, on top of TLS.
- Select-before-operate adds a per-command interlock.

Generate lab certificates with `./scripts/gen_certs.sh`. For real use, issue
certificates from your own CA with proper key protection and peer allow-listing. See
the project SECURITY.md.
