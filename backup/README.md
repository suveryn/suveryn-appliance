# Backups (tracker #33)

`suveryn-backup` makes the appliance's backups: nightly (`systemd/suveryn-backup.timer`, 02:30 in the
office's time zone, or at the next start if the appliance was off), onto the target the web setup saved
in `/etc/suveryn/backups.json`, encrypted for the office's recovery key.

| | |
|---|---|
| **What** | Both PostgreSQL databases (`suveryn`: documents, passages, conversations, cases, usage, settings; `keycloak`: accounts and sign-in settings) and the office's part of the data partition: `/var/lib/suveryn/etc` (`/etc/suveryn`: settings, secrets, certificates), `host`, `network`, `nginx`. Not the software or the models (the image and the signed bundles bring them), not the appliance's identity (sealed to this machine's TPM) |
| **Where** | A USB disk (ext4, exFAT, FAT or NTFS) or a share on the office's network (SMB 3 or NFS), mounted for the run only, `nosuid,nodev,noexec`, into `suveryn-backups/<appliance name>/` |
| **Encrypted** | With [age](https://age-encryption.org) to the recovery key made in the browser during setup. The appliance has only the public half (`age1…`): it writes backups and can't read them. The office keeps the private half (`AGE-SECRET-KEY-1…`) offline |
| **Kept** | The retention chosen in setup (14, 30, 90 or 365 days). After a successful backup, older ones are deleted; the newest is always kept; a failed run deletes nothing |
| **Seen** | `/var/lib/suveryn/backup/status.json`: the last success, the last failure and its step, the backups on the target. The gateway shows it to the administrator |

```bash
sudo suveryn-backup run                                   # now (what the timer runs)
suveryn-backup status                                     # the last success and failure
sudo suveryn-backup list                                  # what is on the target
suveryn-backup check FILE --identity recovery.key         # decrypt and check every part, restore nothing
sudo suveryn-backup restore FILE --identity recovery.key --yes   # onto this appliance
```

(On the appliance the tool is `/opt/suveryn/appliance/backup/suveryn-backup`.)

## Restoring onto a replacement machine

1. Install the appliance from the stick and go through the web setup (any answers: the restore
   replaces them).
2. Put the recovery key in a file on the appliance (from the office's offline copy) and the backup on
   a USB disk or the share.
3. `suveryn-backup check` it, then `suveryn-backup restore … --yes`. The services stop; both databases
   are replaced: each is restored beside the live one first, and only once that has worked is the live
   one dropped and the restored one put in its place, so a failed restore leaves the appliance as it
   was; the office's settings, secrets and certificates
   come back; the database roles get the passwords those settings use; the services start again.
4. Delete the key file. The new machine has its own identity: register it with the support portal.

A web setup option for this is a ticket of its own. The restore is tested in `tests/test_backup.py`:
a backup made, checked, and restored onto changed databases against a real PostgreSQL with pgvector.
