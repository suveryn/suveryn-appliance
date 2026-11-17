# Keycloak

Keycloak, the appliance's sign-in: its configuration, the production realm and the sūveryn theme,
installed by [`suveryn-provision`](../provision/README.md).

| File | What |
|---|---|
| [`keycloak.conf.example`](keycloak.conf.example) | Keycloak's production configuration, copied as `conf/keycloak.conf` |
| [`quarkus.properties`](quarkus.properties) | The one setting `keycloak.conf` has no option for: a pooled database connection is checked before use |
| [`realm/`](realm/README.md) | The production realm, built from suveryn-core's development realm |
| [`theme/`](theme/README.md) | The sūveryn theme: login pages, account console and emails |
