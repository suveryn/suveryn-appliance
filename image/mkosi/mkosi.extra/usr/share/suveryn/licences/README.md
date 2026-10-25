# Third-party licence texts shipped on the appliance

The image has no `/usr/share/doc` (`WithDocs=no` in `mkosi.conf`), so licences that must reach the
office with the software are kept here instead, at `/usr/share/suveryn/licences/` on the appliance.

| File | What it covers | Why it is here |
|---|---|---|
| `nvidia-graphics-drivers-580.copyright` | Ubuntu's `nvidia-utils-580`, `libnvidia-compute-580`, `nvidia-firmware-580` and the `kernel-open` source of `linux-modules-nvidia-580-open-generic` | The NVIDIA Driver License Agreement (§1.1(d)) permits distribution only if the agreement is provided to each recipient (development context §2, tracker #49) |

The file is the unchanged `copyright` file of `nvidia-utils-580` 580.178.04-0ubuntu0.26.04.1
(resolute), the same text as 24.04's 580.178.04-0ubuntu0.24.04.1. When the driver branch or the package changes, replace it with that package's `copyright`
file; `mkosi.postinst.chroot` fails a build that installs the driver without this file.
