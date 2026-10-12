# The sūveryn appliance image: a bare-metal disk image (raw, UEFI, GPT), flashed onto the office's
# own server, not a VM image. Packer and QEMU are only the build tools: Ubuntu 24.04 is installed
# into a disk file in a QEMU machine, provisioned, hardened, and the disk file is the image.
#
#   cd image && packer init . && PKR_VAR_build_password=$(openssl rand -base64 24) packer build .
#   (no KVM, e.g. a RunPod pod: add -var accelerator=tcg; much slower)
#
# Build time (this template) vs first boot (suveryn-first-boot.service): see README.md.

packer {
  required_version = "= 1.16.1"
  required_plugins {
    qemu = {
      version = "= 1.1.6"
      source  = "github.com/hashicorp/qemu"
    }
  }
}

variable "accelerator" {
  type        = string
  default     = "kvm"
  description = "kvm, or tcg (software emulation) where /dev/kvm is missing"
}
variable "cpus" {
  type    = number
  default = 16
}
variable "memory" {
  type    = number
  default = 32768
}
variable "build_password" {
  type        = string
  sensitive   = true
  description = "the temporary build user's password; the user is deleted before the image is finished"
}
variable "output_dir" {
  type        = string
  default     = "/var/tmp/suveryn-image"
  description = "where the image is written: outside this repository, which is uploaded into the build"
}

locals {
  appliance = abspath("${path.root}/..")
}

source "qemu" "suveryn" {
  iso_url      = "https://releases.ubuntu.com/24.04/ubuntu-24.04.5-live-server-amd64.iso"
  iso_checksum = "sha256:97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8"

  accelerator = var.accelerator
  cpu_model   = var.accelerator == "kvm" ? "host" : "max" # emulated: a modern CPU (AVX2), as PyTorch expects
  cpus        = var.cpus
  memory      = var.memory
  headless    = true

  # The image: one raw disk. UEFI only (every server since ~2015); the installed system boots from the
  # removable EFI path, so it starts on any machine without a boot entry being created first.
  disk_size          = "48G"
  format             = "raw"
  disk_interface     = "virtio"
  disk_discard       = "unmap"
  disk_detect_zeroes = "unmap"
  efi_boot           = true
  efi_firmware_code  = "/usr/share/OVMF/OVMF_CODE_4M.fd"
  efi_firmware_vars  = "/usr/share/OVMF/OVMF_VARS_4M.fd"
  output_directory   = var.output_dir
  vm_name            = "suveryn-appliance.img"

  # Ubuntu's autoinstall, answers served by Packer's HTTP server during the build only.
  http_content = {
    "/user-data" = templatefile("${path.root}/http/user-data.pkrtpl", { password_hash = bcrypt(var.build_password) })
    "/meta-data" = ""
  }
  boot_wait = "5s"
  boot_command = [
    "e<wait><down><down><down><end>",
    " autoinstall ds=nocloud-net\\;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/",
    "<f10>",
  ]

  communicator = "ssh"
  ssh_username = "suveryn-build"
  ssh_password = var.build_password
  ssh_timeout  = "8h" # the installer alone takes an hour or more without KVM

  # The build user and the SSH server exist for the build only: both are removed here, last.
  shutdown_command = "echo '${var.build_password}' | sudo -S sh -c 'userdel -f -r suveryn-build; rm -f /etc/sudoers.d/90-suveryn-build; apt-get purge -y -qq openssh-server openssh-sftp-server >/dev/null; rm -f /etc/ssh/ssh_host_*; shutdown -P now'"
  shutdown_timeout = "30m"
}

build {
  sources = ["source.qemu.suveryn"]

  # This repository, as one archive (without .git and build output): the provisioning script, the
  # reverse proxy, the reader sandbox, the network-mode tool, the Keycloak theme and realm, and these
  # image scripts. build.sh unpacks it into /opt/suveryn/appliance.
  provisioner "shell-local" {
    inline = ["tar -C '${local.appliance}' --exclude=.git --exclude='./image/output' --exclude='./keycloak/theme/build' --exclude='__pycache__' -czf '${var.output_dir}-appliance.tar.gz' ."]
  }
  provisioner "file" {
    source      = "${var.output_dir}-appliance.tar.gz"
    destination = "/tmp/suveryn-appliance.tar.gz"
  }

  provisioner "shell" {
    execute_command = "echo '${var.build_password}' | sudo -S -E bash '{{ .Path }}'"
    scripts = [
      "${path.root}/scripts/build.sh",   # NVIDIA driver, CUDA build tools, suveryn-provision image
      "${path.root}/scripts/harden.sh",  # packages, services, firewall, kernel and login settings
      "${path.root}/scripts/cleanup.sh", # build tools and caches out, identity reset, free space zeroed
    ]
    timeout = "12h"
  }

  post-processor "shell-local" {
    inline = [
      "cd ${var.output_dir}",
      "zstd -T0 -10 --rm -q suveryn-appliance.img",
      "sha256sum suveryn-appliance.img.zst > SHA256SUMS",
    ]
  }
}
