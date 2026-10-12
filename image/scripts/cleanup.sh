#!/bin/bash
# Last build step, run as root inside the image (Packer): build tools and caches out, the
# machine's identity reset (every flashed box gets its own), free space zeroed so the image
# compresses. The build user and the SSH server go in Packer's shutdown command, last.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== Build tools out (llama-server keeps the CUDA runtime it links to)"
apt-get purge -y -qq 'cuda-nvcc-*' 'cuda-cudart-dev-*' 'libcublas-dev-*' 'cuda-crt-*' 'cuda-nvvm-*' cmake build-essential \
  >/dev/null 2>&1 || true
apt-get autoremove -y -qq --purge >/dev/null
apt-get clean
rm -rf /opt/suveryn/node /opt/suveryn/core/packages/chat-ui/node_modules /var/lib/suveryn/downloads \
  /var/lib/suveryn/uv-cache /var/lib/suveryn/npm-cache /opt/suveryn/llama.cpp/build/CMakeFiles /root/.cache

echo "== Identity reset: machine-id, cloud-init, logs"
cloud-init clean --logs >/dev/null 2>&1 || true
touch /etc/cloud/cloud-init.disabled
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
find /var/log -type f -exec truncate -s 0 {} +
rm -f /root/.bash_history

echo "== Free space trimmed (zeros in the raw image compress away)"
fstrim -av >/dev/null || true
