#!/bin/bash
# Build-time steps, run as root inside the image (Packer). Everything here is the same for every
# office; nothing unique to a box (names, secrets, certificates, models) is put in the image.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
NVIDIA_BRANCH=580                 # NVIDIA's long-term branch; CUDA 12.8 needs 570 or later
CUDA=12-8                         # the CUDA release llama.cpp is built with (as on the dev pod)
CUDA_KEYRING_SHA256=d2a6b11c096396d868758b86dab1823b25e14d70333f1dfa74da5ddaf6a06dba

echo "== This repository into /opt/suveryn/appliance"
install -d -m 0755 /opt/suveryn
rm -rf /opt/suveryn/appliance
install -d -m 0755 /opt/suveryn/appliance
tar -xzf /tmp/suveryn-appliance.tar.gz -C /opt/suveryn/appliance --no-same-owner
rm /tmp/suveryn-appliance.tar.gz

echo "== Kernel, firmware and tools for bare metal"
apt-get update -qq
# linux-generic (not -virtual): the drivers real hardware needs; linux-firmware: NICs, NVMe, CPUs.
apt-get install -y -qq --no-install-recommends linux-generic linux-firmware cloud-guest-utils zstd \
  ubuntu-drivers-common >/dev/null

echo "== NVIDIA driver $NVIDIA_BRANCH (open kernel modules from Ubuntu's archive, signed by Canonical: Secure Boot works)"
KERNEL_FLAVOUR_PKG=linux-modules-nvidia-${NVIDIA_BRANCH}-open-generic
apt-cache show "$KERNEL_FLAVOUR_PKG" >/dev/null 2>&1 || { echo "no $KERNEL_FLAVOUR_PKG in the archive" >&2; exit 1; }
apt-get install -y -qq --no-install-recommends "$KERNEL_FLAVOUR_PKG" "nvidia-utils-$NVIDIA_BRANCH" \
  "libnvidia-compute-$NVIDIA_BRANCH" >/dev/null

echo "== CUDA $CUDA: the compiler to build llama.cpp (removed again in cleanup.sh) and the runtime it needs"
curl -fsSL -o /tmp/cuda-keyring.deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
echo "$CUDA_KEYRING_SHA256  /tmp/cuda-keyring.deb" | sha256sum -c --quiet
dpkg -i /tmp/cuda-keyring.deb >/dev/null && rm /tmp/cuda-keyring.deb
apt-get update -qq
apt-get install -y -qq --no-install-recommends "cuda-nvcc-$CUDA" "cuda-cudart-dev-$CUDA" "libcublas-dev-$CUDA" \
  "cuda-cudart-$CUDA" "libcublas-$CUDA" >/dev/null
ln -sfn "/usr/local/cuda-${CUDA/-/.}" /usr/local/cuda

echo "== suveryn-provision image"
python3 /opt/suveryn/appliance/provision/suveryn-provision image

echo "== First-start units"
install -m 0644 /opt/suveryn/appliance/image/systemd/*.service /etc/systemd/system/
systemctl enable suveryn-grow-root.service suveryn-first-boot.service
