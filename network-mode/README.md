# Network mode

An installation runs in one of two network modes (development context §5; tracker #38). The appliance
records the mode once, lets any component read it without a network call or a database, and
enforces it with its own firewall.

| Mode | Outgoing | Meaning |
|---|---|---|
| `connected` | `approved` (default) | Reaches only approved destinations: sūveryn updates and models, Ubuntu's security updates, time, and the connectors the office enables. The appliance blocks everything else itself |
| `connected` | `all` | Any program on the appliance may reach the internet. Only for an office whose own firewall decides what the appliance may reach |
| `air-gapped` | `none` | No outgoing connections beyond the office's own network. Updates and extra models arrive on a USB stick or from a mirror on the office network |

In every mode the office's own network stays reachable: private addresses (`10/8`, `172.16/12`,
`192.168/16`, link-local, and their IPv6 equivalents), for LDAP/AD, DNS, time and a local mirror.

The third mode of earlier versions, `outbound-blocked`, was dropped in October 2026: Connected with
approved destinations covers it. A file that still says `outbound-blocked` reads as **unknown**.

## Where it lives

| File | What | Who writes it |
|---|---|---|
| `/etc/suveryn/network-mode.env` | The mode and the outgoing policy (0644, no secret) | `suveryn-network-mode set` |
| `/etc/suveryn/approved-destinations` | The office's own approved destinations, one domain per line | `suveryn-network-mode allow` / `disallow` |
| `/opt/suveryn/network-mode/builtin-destinations` | The appliance's own approved destinations, from [`builtin-destinations`](builtin-destinations); can't be removed on the appliance | The image |
| `/etc/suveryn/dnsmasq.conf` | The resolver's configuration, made from the above | `suveryn-network-mode apply` |

```bash
# The installation's network mode, written by suveryn-network-mode. …
SUVERYN_NETWORK_MODE=connected
SUVERYN_OUTBOUND=approved
```

`SUVERYN_OUTBOUND` is `approved` or `all` for `connected`, and `none` for `air-gapped`. A connected
file without it reads as `approved`, the stricter of the two.

## Setting it

```bash
sudo suveryn-network-mode set connected                  # approved destinations only
sudo suveryn-network-mode set connected --outbound all
sudo suveryn-network-mode set air-gapped
sudo suveryn-network-mode allow portal.example-connector.com
sudo suveryn-network-mode disallow portal.example-connector.com
sudo suveryn-network-mode apply                           # load the rules for what is recorded
```

`set` refuses anything but these values and writes the file atomically, so a reader never sees half a
file. `allow` takes a domain name, which also covers its subdomains; it refuses anything that isn't
a domain name and any name the outgoing-traffic check uses as an address that must stay unreachable.
The appliance's own destinations can't be removed. Change the mode with the tool, never by editing
the file, then run `apply` and restart the services that read the mode (the gateway and Keycloak).

Provisioning sets it from `--network-mode` and `--outbound` (`NETWORK_MODE` and `OUTBOUND` in
`first-boot.env`). The web setup (#37) sets it the same way: it runs `suveryn-provision first-boot
--network-mode <mode>` (and `--outbound` when connected) with the office's answer. The mode is **declared, not
measured**, but on the appliance it is also enforced (below), and the check says whether that holds.

## Reading it (any component)

- **As environment variables.** Add the file to the service's systemd unit, and the process gets
  `SUVERYN_NETWORK_MODE` and `SUVERYN_OUTBOUND`:

  ```ini
  [Service]
  EnvironmentFile=-/etc/suveryn/network-mode.env
  ```

  The `-` keeps a service starting when the file is missing; the variables are then unset, which
  means unknown. The file is also valid shell (`. /etc/suveryn/network-mode.env`).
- **From the file**: read the `SUVERYN_NETWORK_MODE=` and `SUVERYN_OUTBOUND=` lines, ignoring blank
  lines and lines starting with `#`.
- **From a shell script:** `suveryn-network-mode get` prints the mode, `get --outbound` the policy;
  each prints `unknown` and exits 1 when no valid mode is recorded.

**Unknown** means the file is missing, a key is given twice, a value isn't one of the above, or the
policy doesn't fit the mode. A component must then take the safe reading for its purpose: **never
claim to be air-gapped, and never rely on outbound access either.** Don't set these variables
anywhere else: one source.

Read by: the gateway (`suveryn-gateway.service`) and Keycloak (`keycloak.service`), whose login page
shows its "Air-gapped" badge only when the mode is `air-gapped` ([`keycloak/theme`](../keycloak/theme/README.md#the-air-gapped-badge)).

## How the appliance enforces it

What comes in is decided apart from the mode. On the read-only system it is the image's fixed
nftables table `inet suveryn_inbound` (`suveryn-inbound.service`): 80 and 443, DHCP's answers and mDNS.
On an appliance on an ordinary Ubuntu (the Packer test image) it is ufw: 80 and 443. A test machine
(`install`, or `first-boot --test-machine`) gets neither. What goes out is decided by the network
mode, in two parts that `apply` writes and loads:

1. **`suveryn-egress`** (systemd, before the network comes up): an nftables table, `inet
   suveryn_egress`, on outgoing traffic. It lets out: loopback; connections already established; the
   office's own network; mDNS on the office's link (224.0.0.251 and ff02::fb, port 5353, for
   `<name>.local`); the resolver's DNS queries (as user `suveryn-dns`); and, with approved
   destinations only, web (80, 443) and time (123) traffic to addresses in two sets, `approved4` and
   `approved6`. Everything else is refused at once (logged, at most 10 a minute, prefix
   `suveryn-egress refused:`). With `--outbound all` the table isn't loaded.
2. **`suveryn-dns`**: the appliance's own resolver (dnsmasq) on `127.0.0.1`, which every program on
   the appliance uses (`/etc/resolv.conf`). It asks the office's DNS servers (from DHCP, via
   systemd-resolved) and, as it answers a lookup for an approved name or a subdomain of one, puts the
   address into the sets. Entries expire after an hour and the resolver caches answers for at most
   five minutes, so a cached answer always has its address in the set.

So a program can only reach an approved destination by looking its name up through the appliance's
resolver, and an address it got any other way is refused.

Without a valid mode, `apply` lets nothing out but the office's own network (fail closed).

**Limits, stated plainly:**

- The rules work by address. An approved name that shares its address with other sites (a CDN)
  also lets those sites through on that address.
- DNS lookups for any name go to the office's DNS servers, as in every network. Whatever the office's
  DNS forwards is outside the appliance's control.
- Anything with root on the appliance can change the rules. The check (below) reports when they
  aren't loaded.
- In `all`, the appliance doesn't limit outgoing traffic at all; the office's own firewall must.

## The outgoing-traffic check

```bash
suveryn-network-mode check          # passed / FAILED, with what was tried
suveryn-network-mode check --json   # the same for the web setup's last screen and the admin dashboard
```

It checks that the appliance's own rules are loaded as the mode needs, then tries to connect to
addresses that are never approved (`example.com`, `1.1.1.1`, `9.9.9.9` and an IPv6 address, port
443). It **passes** when the rules are loaded and none of those is reached, and **fails** when the
rules aren't loaded, when no valid mode is recorded, or when any of them is reached: then traffic gets
out, for example through a proxy on the office network. With `--outbound all` it passes when the
table is absent and reports that nothing is limited. `suveryn-provision verify` runs it on the
appliance. Exit code 0 when it passes, 1 when it doesn't.

The JSON holds the mode and policy, `firewall_active`, `passed`, a one-line `summary`,
`outside_reached` (the targets that were reached), each probe with its result, and the approved
destinations.

## Tests

```bash
python3 -m pytest network-mode/tests
```

- `test_network_mode.py`, any machine: the mode file (every mode and policy, atomic writes, valid
  as an environment file and shell, every kind of unknown, including `outbound-blocked`), the
  approved destinations (adding, removing, what can't be approved), and the rules and resolver
  configuration made from them; as root, also that `nft` accepts the rules.
- `test_egress.py`, Linux as root with `ip`, `nft` and `dnsmasq`: the rules and resolver for real, in
  two network namespaces (the appliance, and an office network with a DNS server and the internet).
  Approved names are reachable once looked up, including subdomains, and not before; other addresses
  are refused by name and by address; the office network stays reachable; the office adds and
  removes a destination; air-gapped and all; and the check passing, and failing when an outside
  address gets through or the rules aren't loaded; and no valid mode letting nothing out.

**Where the namespace tests run.** `test_egress.py` (7 tests) and `test_nft_accepts_the_rules` (3)
need their own network namespace with nftables in it. A VM or a privileged container allows that; an
unprivileged container, such as the RunPod pod, doesn't. `conftest.py` checks once whether this
machine allows it and otherwise **skips** those 10 tests with the reason (`pytest -rs` shows it). On
a machine that is meant to run them, set `SUVERYN_REQUIRE_EGRESS_TESTS=1`: a missing requirement
then **fails** them instead, so a skip can't hide a problem.

```bash
sudo python3 -m pytest -rs network-mode/tests                                  # anywhere
sudo SUVERYN_REQUIRE_EGRESS_TESTS=1 python3 -m pytest network-mode/tests       # a VM or privileged container
```
