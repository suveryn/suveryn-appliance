#!/bin/bash
# Build llama-server for the appliance image on a host, instead of inside the image's build machine,
# where without KVM the CUDA compiler takes many hours. The host must be what the image is: Ubuntu
# 24.04 with CUDA 12.8 (e.g. a RunPod pod). The build is the image's: the pinned commit, CUDA for
# Ampere, Ada and Hopper, generic CPU code (SUVERYN_IMAGE_BUILD=1), whatever GPU the host has.
#
#   sudo image/prebuild-llama.sh /var/tmp/suveryn-llama.tar.gz
#   packer build -var llama_prebuilt=/var/tmp/suveryn-llama.tar.gz …
set -euo pipefail
out=${1:?usage: prebuild-llama.sh <output.tar.gz>}
here=$(cd "$(dirname "$0")/.." && pwd)
. /etc/os-release
[ "$VERSION_ID" = "24.04" ] || { echo "the host must be Ubuntu 24.04, like the image (not $VERSION_ID)" >&2; exit 1; }
nvcc --version 2>/dev/null | grep -q "release 12.8" || /usr/local/cuda/bin/nvcc --version | grep -q "release 12.8" \
  || { echo "the host needs CUDA 12.8, like the image" >&2; exit 1; }
SUVERYN_IMAGE_BUILD=1 python3 "$here/provision/suveryn-provision" llama-server
grep -q "^GGML_NATIVE:BOOL=OFF" /opt/suveryn/llama.cpp/build/CMakeCache.txt \
  || { echo "/opt/suveryn/llama.cpp was built for this host, not the image; remove its build folder and run again" >&2; exit 1; }
tar -C /opt/suveryn/llama.cpp -czf "$out" .git build/bin
sha256sum "$out"
