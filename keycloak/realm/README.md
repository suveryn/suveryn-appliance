# Production realm

The appliance's Keycloak realm, built from suveryn-core's development realm (suveryn-tracker#16).
One source: [`suveryn-realm.json`](https://github.com/suveryn/suveryn-core/blob/main/packages/api-gateway/keycloak/suveryn-realm.json)
in suveryn-core defines the client, its flow, the roles and the password and brute-force rules.
`build_realm.py` takes it at the commit in `realm-lock.json` (checked by SHA-256) and lays the
production settings over it.

```bash
python3 keycloak/realm/build_realm.py --public-url https://suveryn.office.example --out /root/suveryn-realm
kc.sh import --file /root/suveryn-realm/suveryn-realm.json   # or the admin console; then delete the file
```

It writes two files, both 0600 and git-ignored here:
- `suveryn-realm.json`: to import once, then delete, because it holds the client secret;
- `gateway-oidc.env`: `SUVERYN_OIDC_ISSUER`, `SUVERYN_OIDC_CLIENT_ID`, `SUVERYN_OIDC_CLIENT_SECRET` and
  `SUVERYN_PUBLIC_URL` for the gateway's environment file.

## What production changes

| Setting | Development | Production | Why |
|---|---|---|---|
| `sslRequired` | `external` | `all` | `external` lets private addresses use plain HTTP, which in an office means every workstation |
| Sign-in events | off | on, kept 180 days | Failed sign-ins, lock-outs and logins can be investigated; username, IP and time only |
| Admin events | off | on, kept 180 days, no details | Who changed which user, role or client; not the changed data itself |
| Redirect, base and logout URLs | `http://localhost:5173/…` | exactly `<public-url>/…` | No wildcards: a wildcard lets a crafted link send the sign-in code elsewhere |
| Themes | Keycloak's | `suveryn` (login, account, email) | This repository's [theme](../theme/README.md) |
| Client secret | none in the file | new random one per build | Rotation: build again and re-import the client |

Everything else comes unchanged from the development realm:
- the authorisation-code flow with PKCE S256 only;
- no password grant;
- brute-force protection;
- password length at least 12;
- no self-registration and no external identity providers.

`build_realm.py` refuses to build if the development realm ever loses one of these.

Event retention is 180 days, decided on 2026-10-11. Long enough to investigate an incident found months later, short enough for data minimisation. Keycloak deletes older events itself: sign-in events through the realm's `eventsExpiration`, admin events through its `adminEventsExpiration` attribute, which Keycloak 26.8's event store reads. Keycloak's own log (the `jboss-logging` listener) goes to the journal, whose retention the appliance sets.

## Keycloak itself

[`../keycloak.conf.example`](../keycloak.conf.example) is the production configuration:
- a fixed `hostname` (`https://<office>/idp`), so issuer and redirects never come from request headers;
- `proxy-headers=xforwarded` behind the appliance's TLS proxy, listening on loopback only;
- PostgreSQL, with the password from the environment, never from the file;
- health checks on, metrics off.

With `sslRequired: all`, the gateway must reach Keycloak through its public https URL. That is the issuer in `gateway-oidc.env`. The appliance must therefore resolve its own name to itself. Plain http to the realm is refused (checked: 403).

## Tested

**Tests** (`python3 -m pytest keycloak/realm/tests`, with a suveryn-core checkout next to this one):
- the overrides are applied;
- everything else equals the development realm;
- weakened development realms are refused;
- the public URL must be plain https;
- each build gets a new secret, written 0600;
- the built files can't be committed.

**Imported into Keycloak 26.8.0** on the dev pod as a temporary realm, read back through the admin API, then deleted (2026-10-11):
- every setting above arrived as intended, including the client's exact URLs, PKCE S256 and the admin-events expiry;
- http requests to the realm were refused.

**Read by Keycloak 26.8.0** (`kc.sh show-config`): `keycloak.conf.example` has no unknown or rejected settings. It has not been started in production mode, because the dev pod has no TLS proxy. Do that on the appliance image.
