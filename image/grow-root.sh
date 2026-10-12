#!/bin/bash
# Grow the root partition and its ext4 file system to the whole disk (first start, online).
set -euo pipefail
root=$(findmnt -no SOURCE /)
part=$(basename "$root")
disk=/dev/$(lsblk -no PKNAME "$root")
number=$(cat "/sys/class/block/$part/partition")
growpart "$disk" "$number" || [ $? -eq 1 ]   # 1: already the whole disk
resize2fs "$root" >/dev/null
touch /var/lib/suveryn/.root-grown
echo "root file system: $(df -h / | awk 'NR==2 {print $2}') on $disk"
