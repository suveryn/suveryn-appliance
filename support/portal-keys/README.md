# Portal keys

The public keys the appliance trusts for support manifests ([`../README.md`](../README.md)): every
`*.pem` file here (Ed25519). None yet: the portal's key is still to be made, so no appliance accepts a
manifest until it is added here and released.

Adding one (Sūveryn Labs, once per key):

```bash
support/suveryn-manifest public-key --aws-kms alias/suveryn-portal --region eu-west-1 > support/portal-keys/portal.pem
```

A key is retired by a release without its file. Tests use keys they make themselves
(`SUVERYN_PORTAL_KEYS`), never these.
