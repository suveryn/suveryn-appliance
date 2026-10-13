#!/bin/bash
# Build the image's heavy parts on a host instead of inside the image's build machine, where without
# KVM they take hours (the CUDA compiler) or crash (Java's compiler, for Keycloak's build):
#   llama-server   pinned commit, CUDA for Ampere/Ada/Hopper, generic CPU code
#   Keycloak       pinned release, its build with the sūveryn theme
#   the chat UI    suveryn-core's packages/chat-ui at the pinned commit, pinned Node
# The host must be what the image is: Ubuntu 24.04 with CUDA 12.8 (e.g. a RunPod pod). Each part
# carries what it was built from (.suveryn-built, the git commit); suveryn-provision image checks
# that and keeps a matching part, else builds it itself.
#
#   sudo image/prebuild.sh /var/tmp/suveryn-prebuilt.tar.gz
#   packer build -var prebuilt=/var/tmp/suveryn-prebuilt.tar.gz …
set -euo pipefail
out=${1:?usage: prebuild.sh <output.tar.gz>}
here=$(cd "$(dirname "$0")/.." && pwd)
. /etc/os-release
[ "$VERSION_ID" = "24.04" ] || { echo "the host must be Ubuntu 24.04, like the image (not $VERSION_ID)" >&2; exit 1; }
PATH=/usr/local/cuda/bin:$PATH
nvcc --version | grep -q "release 12.8" || { echo "the host needs CUDA 12.8, like the image" >&2; exit 1; }
python3 "$here/provision/suveryn-provision" prebuild
grep -q "^GGML_NATIVE:BOOL=OFF" /opt/suveryn/llama.cpp/build/CMakeCache.txt \
  || { echo "/opt/suveryn/llama.cpp was built for this host, not the image; remove its build folder and run again" >&2; exit 1; }
tar -C / --exclude=opt/keycloak/data -czf "$out" opt/suveryn/llama.cpp/.git opt/suveryn/llama.cpp/build/bin opt/keycloak opt/suveryn/ui
sha256sum "$out"
