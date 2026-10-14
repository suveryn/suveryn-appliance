# Partition definitions for the appliance's disk

systemd-repart definitions for the whole disk (docs/design/appliance-architecture.md, "The disk").
The release image brings the EFI partition and slot A; these definitions match those and add slot B
(empty) and the data partition. The installer (#37) applies `appliance/` to the chosen disk;
`test/run-vm-tests` applies `test/` (the same partitions, smaller) to the test system's disk.
