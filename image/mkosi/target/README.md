# Partition definitions for the appliance's disk

systemd-repart definitions for the whole disk (docs/design/appliance-architecture.md, "The disk"),
used by the installer's disk step, `/usr/lib/suveryn/install-disk` (#34, #37). On an empty disk they
create: the EFI partition and slot A with its verity, copied block for block from the installer stick
(`CopyBlocks=`; install-disk names the stick's EFI partition); slot B and its verity, empty; and the
data partition, LUKS2-encrypted, with the machine-id file. `appliance/` is the appliance's sizes;
`test/` the same partitions, small, for the test system (`test/run-vm-tests`). The image carries
the right set in `/usr/lib/suveryn/repart.d`.
