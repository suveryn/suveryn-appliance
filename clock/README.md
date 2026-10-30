# The clock

`suveryn-clock` keeps the appliance's clock right where no time server does (Filip, 30 October 2026), from the
browsers of the people signed in, through suveryn-core's gateway (its README, *The clock*).

**Why.** Two-step sign-in accepts a code from one 30-second step either side of the appliance's clock. A phone
keeps its time from the mobile network; the appliance keeps it with `systemd-timesyncd`: `ntp.ubuntu.com` in
connected mode, or the time server the office's network hands out (DHCP). Air-gapped, with no time server on the
office's network, the clock drifts a few seconds a day, and after weeks or months every code is refused, the
administrators' included.

**How.** The gateway runs as its own user and can't change the clock. It leaves a request in
`/run/suveryn/clock-requests` (`tmpfiles/suveryn-clock.conf`, in memory, the gateway's own, 0700);
`suveryn-clock-request.path` starts `suveryn-clock request` as root, which deletes it, acts within its own bounds
and answers in `/var/lib/suveryn/clock/result.json` (0644, no secret).

| Request | When | What, at most |
|---|---|---|
| `nudge` | The browsers of at least two accounts, or one administrator, agree within two seconds that the clock is off | Half a second to a minute, slewed (`adjtime`): never a jump, at most about 43 seconds a day, once an hour |
| `set` | An administrator's *Set to this PC's time* on Status, after a confirmation that shows both times | At once, at most a year either way |

Both are refused while a time server keeps the clock (`timedatectl`'s `NTPSynchronized`). Afterwards the hardware
clock is written (`hwclock --systohc --noadjfile`, from `util-linux-extra`, in the image), so a restart keeps the
correction; the answer's `hardware_clock` says whether that worked. Where the system may not set the clock at all
(a container), the answer is a refusal (`adjtime: …`, `clock_settime: …`), never a stopped tool.

**Why the bounds.** Moved forward, the gateway's hourly sweep deletes closed cases whose keep period has then
passed, and backups past their retention; moved back, an old two-step code is valid again. The gateway has its
own limits; these hold whatever it asks.

The unit keeps two capabilities: setting the clock, and reading and deleting the gateway's request in its folder.

## Tests

```bash
python3 -m pytest clock/tests
```

They run the tool against a test folder (`SUVERYN_CLOCK_TEST=1`), never this machine's clock.
