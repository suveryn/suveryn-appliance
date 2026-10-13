# Reader sandbox

suveryn-core reads uploaded PDFs in a separate process (`suveryn_rag.isolation`): the parsers
(pypdfium2, qpdf, Ghostscript, Tesseract, Docling) never run inside the gateway, the reader gets no
secrets in its environment, and it can only answer in JSON. On an appliance this folder adds the
operating-system side (suveryn-tracker#15):

| File | What |
|---|---|
| `suveryn-reader-sandbox` | The wrapper suveryn-core starts the reader with (`SUVERYN_EXTRACT_WRAPPER`). |
| `provision-reader.sh` | Creates the reader's user, group and folders, and installs python3-seccomp. Idempotent. |
| `tests/` | Checks the sandbox from the inside (`probe.py`), as root on Linux. |

## What the sandbox does

- **Its own user**, `suveryn-reader`: it can't read the gateway's secrets file, its work folder or its
  memory (`/proc`). Children (Ghostscript, Tesseract) inherit the user.
- **No network**: a seccomp filter refuses every socket except local (AF_UNIX) ones, so a compromised
  parser can't send a document anywhere. Works without network namespaces, so also in containers.
- **Fewer kernel interfaces**: ptrace, process_vm_*, mount and namespace calls, module loading, kexec,
  bpf, perf events, keyrings, userfaultfd and io_uring are refused; 32-bit entry points kill the
  process.
- **no_new_privs**, no core dumps, umask 077, HOME and TMPDIR in the reader's own folder.
- **It fails closed**: started as anyone but root or `suveryn-reader`, or without python3-seccomp, it
  exits instead of running the reader unsandboxed. The gateway then reports document handling as
  unavailable.

## Setting it up

```bash
sudo reader-sandbox/provision-reader.sh <gateway-user> /var/lib/suveryn/inbox /var/lib/suveryn/reader
sudo install -m 0755 reader-sandbox/suveryn-reader-sandbox /opt/suveryn/bin/
```

The gateway's environment:

```bash
SUVERYN_EXTRACT_WRAPPER=/opt/suveryn/bin/suveryn-reader-sandbox
SUVERYN_EXTRACT_INBOX=/var/lib/suveryn/inbox          # owner the gateway, group suveryn-reader, 2750
SUVERYN_EXTRACT_WORK_DIR=/var/lib/suveryn/reader      # owner suveryn-reader, 0700
```

A gateway running as root (the dev pod) is enough as is: the wrapper switches to `suveryn-reader`.
A gateway running as its own user needs sudo to start the wrapper as the reader. With the sudoers lines
below, set `SUVERYN_EXTRACT_WRAPPER="sudo -n -u suveryn-reader /opt/suveryn/bin/suveryn-reader-sandbox"`
([`provision/suveryn-provision`](../provision/README.md) does both):

```
Defaults:<gateway-user> !requiretty
Defaults>suveryn-reader env_keep += "HF_HOME SUVERYN_EXTRACT_WORK_DIR SUVERYN_OCR_JOBS CUDA_VISIBLE_DEVICES"
<gateway-user> ALL=(suveryn-reader) NOPASSWD: /opt/suveryn/bin/suveryn-reader-sandbox *
```

`env_keep` keeps exactly the variables the reader needs, for that one target user. sudo refuses
`--preserve-env=…` without the `SETENV` tag, and `SETENV` would let the gateway pass the reader any
variable, so neither is used.

This sudo route is tested by `suveryn-provision verify` ([`provision`](../provision/README.md)), on a machine provisioned with a gateway running as its own user: a PDF is read through it, and `tests/probe.py`, started exactly as the gateway starts the reader, must find itself running as `suveryn-reader` without network sockets, unable to read the gateway's secrets, without ptrace or a way to root, under seccomp and no_new_privs. That test found that sudo refused `--preserve-env` (hence `env_keep` above).

The code the reader runs (suveryn-core's virtual environment), the model cache (`HF_HOME`) and the
GPU devices must be readable by `suveryn-reader`; nothing under a 0700 home folder.

## Keeping the parsers patched

Ghostscript, Tesseract and qpdf come from the distribution. The appliance image keeps them current
with the distribution's security updates (unattended-upgrades); the Python parsers (pypdfium2,
pikepdf, Docling) are pinned in suveryn-core's `uv.lock` and updated with it.

## Tested

On the dev pod (Ubuntu 24.04 container, RTX 4090), with the real gateway, Docling and OCR, 2026-10-11:
- a born-digital, a scanned and a scanned-and-signed fictional deed were all read through the sandbox;
- during processing, the reader and its Tesseract processes ran as `suveryn-reader`, with seccomp and
  no_new_privs, and even root could not read their environment;
- afterwards, the inbox was empty and no intermediate files were left in the gateway's folder.

`tests/` checks the rest from inside the sandbox: no network sockets, no ptrace, unshare or io_uring,
no access to a 0600 secrets file or to `/root`, no way back to root, children sandboxed too, and the
refusals.

**Limit on the dev pod:** `/workspace` is a network volume that ignores file permissions, so the dev
secrets there (`keycloak-dev.env`) are readable by every user, the reader included. On an appliance,
secrets live on a normal file system in a 0600 file.
