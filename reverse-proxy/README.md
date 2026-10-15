# Reverse proxy

nginx in front of everything, on one https origin per appliance (development context §3: the chat
UI is served on the same origin as the API):

| Path | Goes to |
|---|---|
| `/` | The chat UI's built files, `/opt/suveryn/ui` (suveryn-core `packages/chat-ui`) |
| `/v1/`, `/auth/`, `/health` | suveryn-core's gateway, `127.0.0.1:8000` |
| `/idp/` | Keycloak, `127.0.0.1:8180` (`keycloak.conf`: `hostname=https://<host>/idp`) |
| `/idp/admin/` | Keycloak's admin console, from private and loopback addresses only |
| `/docs`, `/redoc`, `/openapi.json` | 404: FastAPI's API pages load scripts from public CDNs (and `/docs` is off in the gateway too) |
| `http://` | 301 to `https://` |

[`provision/suveryn-provision`](../provision/README.md) installs it: it writes
`suveryn.conf.template` (with the public host name) to `/etc/nginx/sites-available/suveryn`, the two
snippets to `/etc/nginx/snippets/`, checks the configuration with `nginx -t`, and verifies the
running proxy.

## Why nginx

It comes from Ubuntu's main archive, so it gets Ubuntu's security updates for as long as the
release is supported; Caddy is only in universe (community-maintained, an older 2.6). Caddy's
strength, automatic certificates over ACME, can't work in an air-gapped office anyway. nginx's known
trap, that `add_header` in a `location` silently drops the server's headers, is handled by one
snippet that every such location includes, and by tests that fail when one doesn't.

## TLS

TLS 1.2 and 1.3 only, forward-secret ciphers (Mozilla's "intermediate" profile), no session tickets.
The certificate is either:

- **the office's own**: `suveryn-provision install --tls-cert <full chain> --tls-key <key>`; or
- **one from a local certificate authority** made on the machine: its key never leaves
  `/etc/suveryn/tls` (0600). The office's workstations trust `/etc/suveryn/tls/ca.crt` once; the
  machine trusts it too (`update-ca-certificates`), so the gateway can reach Keycloak at the
  public https URL (`SSL_CERT_FILE` in `gateway.env` points at the system bundle). Server
  certificates are valid 397 days.

`/etc/hosts` maps the public name to `127.0.0.1` on the machine itself, so the gateway's calls to
Keycloak stay on the box.

## Security headers

Modelled on suveryn-website's (`_headers`), without anything it needs for analytics:

| Header | Value | Where |
|---|---|---|
| `Content-Security-Policy` | `default-src 'none'`, and `'self'` for scripts, styles, images, fonts, connections and the manifest; no `'unsafe-inline'`, no `data:`, no other host; `base-uri 'none'`, `form-action 'self'`, `frame-ancestors 'none'` | The UI and the gateway's answers. Keycloak's pages keep the CSP Keycloak sends |
| `X-Frame-Options` | `DENY` | The UI and the gateway |
| `Strict-Transport-Security` | `max-age=31536000` (this host only) | Everything |
| `X-Content-Type-Options` | `nosniff` | Everything |
| `Referrer-Policy` | `no-referrer` | Everything |
| `Permissions-Policy` | camera, microphone, geolocation, payment, USB, topics: off | Everything |
| `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy` | `same-origin` | Everything |

The UI's strict CSP is possible because its build has no inline scripts or styles, no `data:`
URLs and no `eval`: fonts, icons and the Word/Excel libraries are its own files. The browser code is
made to fit the policy, never the other way round: no `'unsafe-inline'`, nonce or hash for a library.
The rules for that code, how a library is checked before it is added, and the two places React Aria
(the UI's component library) would write a `<style>` and how each is handled:
[suveryn-core `docs/content-security-policy.md`](https://github.com/suveryn/suveryn-core/blob/main/docs/content-security-policy.md).
The first-boot web setup (#37) must get this same policy. `server_tokens off` hides nginx's version.

## Confidential data

- **Uploads stream to the gateway** (`proxy_request_buffering off` on `/v1/documents`): nginx never
  spools a PDF to its temporary files.
- **No proxied answer is written to disk** (`proxy_max_temp_file_size 0`): answers and passages
  stay in memory.
- **The access log has no query strings and no referrers** (`log_format suveryn`, `$uri`): a
  sign-in callback carries its one-time code in the query string.
- **Forwarded headers are set, never appended**: the gateway and Keycloak trust `X-Forwarded-*` from
  `127.0.0.1`, so a client's own `X-Forwarded-For` must not get through (it would dodge the
  gateway's per-address sign-in limit).

## Tests

```bash
python3 -m pytest reverse-proxy/tests
```

They hold the configuration to these rules: every location with its own headers includes the shared
ones; the UI and API get the strict CSP and Keycloak doesn't; the CSP `verify` expects is the one
nginx sends; API docs are never routed; nothing confidential reaches disk or the log; forwarded
headers are set; TLS is modern only; the admin console is for the office network. The running
proxy is checked by `suveryn-provision verify`: TLS versions and certificate, the redirect, the
headers on real answers, `/docs`, and a sign-in and an upload end to end over https.
