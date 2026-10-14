# Bundle keys

The public keys this system trusts for signed bundles (tracker #36; `/usr/lib/suveryn/bundle`): every
`*.pem` file here. Two per release: the **current** release key and a **spare**, both Ed25519 keys
in AWS KMS, so that a lost or leaked key can be replaced by an update signed with the other one.

Adding them (Sūveryn Labs, once per key):

```bash
bundles/suveryn-bundle-release public-key --aws-kms alias/suveryn-bundles-current --region eu-west-1 \
  > image/mkosi/mkosi.extra/usr/lib/suveryn/bundle-keys/release-current.pem
bundles/suveryn-bundle-release public-key --aws-kms alias/suveryn-bundles-spare --region eu-west-1 \
  > image/mkosi/mkosi.extra/usr/lib/suveryn/bundle-keys/release-spare.pem
```

Retiring a key: a system update without its file. Its replacement goes in with the same update.
The test image trusts its own test keys instead (`image/mkosi/test/make-test-keys`), never these.
