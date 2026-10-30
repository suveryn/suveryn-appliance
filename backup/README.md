# Backups (tracker #33)

`suveryn-backup` makes the appliance's backups: nightly at the office's hour (`hour` in
`/etc/suveryn/backups.json`, 02:00 local time by default; the hourly `systemd/suveryn-backup.timer` runs
`run --scheduled`, which backs up only then, or at the next start if the appliance was off), onto the target
the web setup saved there, encrypted for the office's recovery key.

| | |
|---|---|
| **What** | Both PostgreSQL databases (`suveryn`: documents, passages, conversations, cases, usage, settings; `keycloak`: accounts and sign-in settings) and the office's part of the data partition: `/var/lib/suveryn/etc` (`/etc/suveryn`: settings, secrets, certificates), `host`, `network`, `nginx`. Not the software or the models (the image and the signed bundles bring them), not the appliance's identity (sealed to this machine's TPM) |
| **Where** | A USB disk (ext4, exFAT, FAT or NTFS) or a share on the office's network (SMB 3 or NFS), mounted for the run only, `nosuid,nodev,noexec`, into `suveryn-backups/<appliance name>/` |
| **Encrypted** | With [age](https://age-encryption.org) to the recovery key made in the browser during setup. The appliance has only the public half (`age1…`): it writes backups and can't read them. The office keeps the private half (`AGE-SECRET-KEY-1…`) offline |
| **Kept** | The retention chosen in setup (14, 30, 90 or 365 days). Older ones are deleted before a backup is written and again after; the newest is always kept |
| **Changed** | After setup, in Administration › Backups (suveryn-core docs/design/admin.md): *Back up now*, the hour, the target (a USB disk, or a share whose test signs in and writes), the retention and a new recovery key. The gateway can't change them itself: it leaves a request in `/run/suveryn/backup-requests` and `suveryn-backup request` (root, `suveryn-backup-request.path`) checks it as the web setup did, saves it and answers in `request-result.json`, never with a password. Old backups stay where they were |
| **Seen** | `/var/lib/suveryn/backup/status.json`: the last success, the last failure and its step, the backups on the target. The gateway shows it to the administrator |

```bash
sudo suveryn-backup run                                   # now (the timer runs run --scheduled)
suveryn-backup status                                     # the last success and failure
sudo suveryn-backup list                                  # what is on the target
sudo suveryn-backup find --identity recovery.key          # the backups on the target this key opens, newest first
sudo suveryn-backup check FILE --identity recovery.key    # decrypt and check every part, restore nothing
sudo suveryn-backup restore FILE --identity recovery.key --yes   # onto this appliance
sudo suveryn-backup key-test                              # the administrator's key test (started by its path unit)
sudo suveryn-backup request                               # the administrator's request (started by its path unit)
```

`check` and `restore` take `--on-target` to read FILE as a backup's name on the target, or `newest`.
`check` needs root: it unpacks into `/var/lib/suveryn/backup`, which is root's, and mounts the target.

| Unit | What it does |
|---|---|
| `suveryn-backup.timer` | Hourly: `suveryn-backup run --scheduled`, which backs up only at the office's hour |
| `suveryn-backup-key-test.path` | The administrator's key test. The gateway leaves the recovery key and a request in `/run/suveryn/backup-key-test` (in memory). The path unit starts `key-test`: the key must open this appliance's newest backup, and every part of it must be there. Nothing is restored. The key is deleted whatever happens. The result goes to `/var/lib/suveryn/backup/key-test.json`, with no secrets, and the gateway shows it |
| `suveryn-backup-request.path` | The administrator's other requests from Administration › Backups: back up now, the USB disks connected, a share's test, new settings. The gateway leaves `request.json` in `/run/suveryn/backup-requests`. The path unit starts `request`, which deletes it at once and answers in `request-result.json`, never with a password |

(On the appliance the tool is `/opt/suveryn/appliance/backup/suveryn-backup`.)

## Restoring onto a replacement machine

From the web setup (suveryn-tracker#56): install the appliance from the stick, open its setup page and
choose **Restore from a backup** on the first screen.

1. Choose where the backups are (the USB disk, or the share with its user name and password) and the
   recovery key file the office stored offline. The appliance keeps the key on its encrypted data
   partition, readable by root only, until the restore has run.
2. The appliance finds the backups there that the key opens (`suveryn-backup --settings … find --json`)
   and checks the newest of them with the key (`suveryn-backup --settings … check <newest> --on-target
   --json`). The screen shows when it was made and by which appliance, offers the older ones to choose
   from, and asks before anything is replaced.
3. `suveryn-provision restore` restores it: the databases and the office's settings, secrets and
   certificates (as below), the language models' settings for this machine, the old appliance's network
   mode and name, the office's site written again where this machine keeps it, then starts the services
   and closes setup. A machine that has only run `suveryn-provision image` can be restored: `image`
   installs the services' units and the network-mode tool, and the restore stops and starts only the
   units there are. The key and the share's password are deleted, whether the restore worked or not.
4. The appliance now answers at the old name (`https://<old name>.local`): sign in with the office's
   usual accounts. The new machine has its own identity: register it with the support portal.

By hand, as root:

1. Put the recovery key in a file on the appliance (from the office's offline copy) and the backup on
   a USB disk or the share.
2. `suveryn-backup check` it, then `suveryn-backup restore … --yes`. The services stop. Both databases
   and the four folders are restored beside the live ones first; only once all of that has worked are
   the databases swapped in one transaction and the folders renamed into place, so a failed restore
   leaves the appliance as it was and says so. The database roles get the passwords the restored
   settings use, and the services start again. A service that doesn't start again after that is said
   apart ("restored; these services didn't start again yet: …"): the restore itself is done and stays
   done, and provisioning's next steps usually bring the service up.
3. Delete the key file. Register the new machine with the support portal.

**Links in the folders.** A restore unpacks with tar's "data" filter, which refuses a link to an absolute
path or out of its folder. A backup with such a link is refused when it is made, before anything is
written, naming the link. There is no exception: the read-only system's time zone, `host/localtime`, a
link to `/usr/share/zoneinfo/<zone>`, isn't in the backup. Its name in the IANA time zone database
(`Europe/Brussels`, `America/New_York`) is, in the manifest (`time_zone`), and the restore makes the link
again from it once the folders are in place: the backup's zone when this appliance has it, else its own.

## A backup is checked before it is trusted

Anyone with the office's public key (it is on the appliance and in the key file) can make a file the
recovery key opens: encryption says who may read a backup, not who made it. So `check` and `restore`
act only on what this appliance expects. The archive may hold only the manifest, this appliance's own
database dumps and the four folders; the manifest's databases must be exactly this appliance's (their
owners come from the appliance, never from the backup), its folders among the four, its appliance name
a host name and its date a date. Anything else and the backup is refused before anything changes. That
a backup comes from the office's own appliance isn't proven yet (backups aren't signed): the restore
screen shows when it was made and by which appliance, for a person to check.

The restore is tested in `tests/test_backup.py`:
a backup made, checked, and restored onto changed databases against a real PostgreSQL with pgvector.
