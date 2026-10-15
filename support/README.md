# Support manifests

Sūveryn Labs' signed statement of what an appliance's support agreement covers (design note
[`first-boot-setup`](../docs/design/first-boot-setup/README.md), "Support agreement"; tracker #35,
#37). The office registers the appliance on the portal (the QR code in the web setup and on the
console) and gets a manifest back; the web setup checks it on the appliance (step 4). Optional:
without one, setup continues and the appliance reminds the administrator (later, in the admin
dashboard). Nothing is ever blocked by it.

| File | What |
|---|---|
| [`suveryn-manifest`](suveryn-manifest) | `verify` (the appliance), `make`, `sign` and `public-key` (Sūveryn Labs) |
| [`portal-keys/`](portal-keys/README.md) | The public keys the appliance trusts for manifests |

## A manifest

One small text file, readable by a person and valid YAML, e.g. `suveryn-manifest-7f3c9a2e.yaml`:

```yaml
# sūveryn support manifest. Signed by Sūveryn Labs: changing any line voids it.
format: suveryn-manifest/1
appliance_id: 7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41
edition: Sūveryn Enterprise
users: 15
valid_until: 2027-09-30
next_check_in: 2026-12-31
connectors: 3
playbooks: 4
issued: 2026-10-15
signature: <key fingerprint, hex> <Ed25519 signature, base64>
```

- **Every field is required**, in a fixed layout: one `key: value` per line, no quotes, lists or
  other YAML. The appliance reads only that, so a manifest can't mean something different to
  another parser.
- **The signature** covers every byte before the `signature:` line, the way a bundle's does
  ([`bundles/`](../bundles/README.md)): the key signs `suveryn-manifest-signature/1` and the SHA-256
  of those bytes, so a key service only ever sees that short text. The fingerprint is the SHA-256 of
  the raw public key, as for bundles and the appliance's identity.
- **The appliance refuses** a manifest that isn't this layout (`manifest_format`), isn't signed by a
  key in `portal-keys/` or was changed after signing (`manifest_signature`), names another appliance
  (`manifest_other_appliance`) or has expired (`manifest_expired`). The web setup shows each in the
  office's language.

## Making and signing (Sūveryn Labs, until the portal does it)

```bash
support/suveryn-manifest make suveryn-manifest-7f3c9a2e.yaml --appliance 7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41 \
    --edition "Sūveryn Enterprise" --users 15 --valid-until 2027-09-30 --next-check-in 2026-12-31 --connectors 3 --playbooks 4
support/suveryn-manifest sign suveryn-manifest-7f3c9a2e.yaml --aws-kms alias/suveryn-portal --region eu-west-1
support/suveryn-manifest verify suveryn-manifest-7f3c9a2e.yaml --appliance 7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41
```

`sign` checks the signature with the key's public half before writing it, as for bundles.
`--key-file PRIVATE.pem` is for tests and development only.

## Open

- **The portal's key**: an Ed25519 key in AWS KMS (`ECC_NIST_EDWARDS25519`, `SIGN_VERIFY`), separate
  from the bundle keys: the portal signs on its own, many times a day, and a bundle key signs
  releases. Its public key goes in `portal-keys/` (see there). Until then no appliance accepts a
  manifest.
- **The fields** follow the design's screen (edition, users, valid until, next check-in, connectors,
  playbooks). What the portal and the check-in (#35) need besides is for when the portal is built.
