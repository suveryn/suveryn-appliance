# Signed bundles

How models (now) and system updates (#40, later) reach an appliance: as a **bundle**, a folder whose
manifest is signed by Sūveryn Labs (tracker #36). The appliance checks the signature against the
keys built into its read-only system, then every file, before anything is used. One verification
path for models and updates. Decisions D7 to D9 in the [design note](../docs/design/appliance-architecture.md#decisions-for-signed-bundles-14-october-2026-36).

| Where | What |
|---|---|
| [`suveryn-bundle-release`](suveryn-bundle-release) | Sūveryn Labs: makes the manifest and signs it (AWS KMS) |
| [`/usr/lib/suveryn/bundle`](../image/mkosi/mkosi.extra/usr/lib/suveryn/bundle) | The appliance: `verify`, `install`, `keys` |
| [`/usr/lib/suveryn/bundle-keys`](../image/mkosi/mkosi.extra/usr/lib/suveryn/bundle-keys/README.md) | The public keys the system trusts: the current release key and a spare |

## A bundle

```
qwen3-30b-a3b-2026.10.1/
  manifest.json     format, kind (model or system), name, version, created; per file: path, size, SHA-256
  manifest.sig      the key's fingerprint and the Ed25519 signature
  model.gguf
  …
```

- **What is signed** is a fixed 108-byte text with the manifest's SHA-256
  (`suveryn-bundle-signature/1`, `manifest-sha256 <hex>`). So the signature covers the manifest,
  and the manifest covers every file; a cloud key service only ever sees those 108 bytes.
- **A key's fingerprint** is the SHA-256 of its raw 32-byte public key, as for the appliance's
  identity (#35).
- Files in the folder that the manifest doesn't list (`.DS_Store`, notes) are ignored and reported,
  never read or copied.

## Checking (the appliance)

```bash
/usr/lib/suveryn/bundle verify /run/stick/qwen3-30b-a3b-2026.10.1 [--json]
/usr/lib/suveryn/bundle install /run/stick/qwen3-30b-a3b-2026.10.1 [--json]   # → /var/lib/suveryn/bundles/model/<name>/<version>
/usr/lib/suveryn/bundle keys
```

A bundle is **refused** when it isn't signed, the signature doesn't match, the key isn't trusted
(or was retired), the manifest is malformed or names a file outside the bundle, or any file is
missing, a link, or differs in size or SHA-256. `install` copies and hashes each file in one read,
so what lands on the data partition is exactly what was checked; it writes to a hidden folder first
and renames it when everything matched, so a failed install leaves nothing. A system bundle is
verified the same way but isn't installed here (the update, #40).

`--json` is for the installer's model screen (C3) and the admin dashboard: `valid`, `error`,
`kind`, `name`, `version`, `key`, `files`, `size`, `ignored`, and for `install` `installed`.

## Making and signing (Sūveryn Labs)

```bash
bundles/suveryn-bundle-release make ./qwen3-30b-a3b-2026.10.1 --kind model --name qwen3-30b-a3b --version 2026.10.1
bundles/suveryn-bundle-release sign ./qwen3-30b-a3b-2026.10.1 --aws-kms alias/suveryn-bundles-current --region eu-west-1
```

`sign` checks the signature with the key's public half before writing `manifest.sig`, so a
signature the appliance would refuse never leaves. `make` refuses links and removes an old
signature (a new manifest needs a new one).

### The keys (AWS KMS, to set up once)

1. Two KMS keys, **key spec `ECC_NIST_EDWARDS25519`, key usage `SIGN_VERIFY`**, in an EU region:
   aliases `alias/suveryn-bundles-current` and `alias/suveryn-bundles-spare`. Allow `kms:Sign` and
   `kms:GetPublicKey` only to the people (or the release job) who sign; nobody needs
   `kms:ScheduleKeyDeletion` day to day.
2. Their public keys into the image (once):
   `bundles/suveryn-bundle-release public-key --aws-kms alias/suveryn-bundles-current --region eu-west-1 > image/mkosi/mkosi.extra/usr/lib/suveryn/bundle-keys/release-current.pem`,
   and the same for the spare (`release-spare.pem`). `test_the_appliance_trusts_two_release_keys`
   then passes (remove its `xfail`).
3. **Replacing a key** (lost, leaked, retired): make a new KMS key, and release a system update
   signed with the remaining good key that trusts the good key and the new one. The old key's
   bundles are refused from then on.

The signing tool needs the AWS CLI v2 and credentials. It uses the plain Ed25519 algorithm
(`ED25519_SHA_512`, message type `RAW`), so any Ed25519 checker, openssl included, verifies it.

## Tests

```bash
python3 -m pytest bundles/tests          # any Linux with openssl 3
```

`test_bundles.py`: a valid bundle with the current or the spare key; a retired key, an unknown key,
no trusted key, a missing or damaged signature, a changed manifest, a changed, resized or missing
file, links (a file or a folder pointing outside), and signed but malformed manifests (paths
outside the bundle, duplicates also by case, bad sizes or hashes, unknown kind or format) are all
refused; installing copies exactly the listed files, is harmless when repeated, refuses another
manifest under the same version and leaves nothing when it fails; signing through a stand-in for
the AWS CLI. In the test system (`image/mkosi/test/run-vm-tests`), test bundles on a second disk
are checked and one is installed onto the encrypted data partition, and stays there across restarts.
