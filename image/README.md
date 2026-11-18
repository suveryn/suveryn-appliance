# Image

The two builds of the appliance's disk image, side by side.

| Folder | What |
|---|---|
| [`mkosi`](mkosi/README.md) | The appliance: the read-only, verified system with the encrypted data partition. The only image an office's records may go on |
| [`packer`](packer/README.md) | The legacy test image: unencrypted, fictional documents only, until it is retired (#60) |

The spec for both is the design note, [appliance-architecture.md](../docs/design/appliance-architecture.md).
