# Dnsmasq WebConf

[![CI](https://github.com/akivajp/dnsmasq-webconf/actions/workflows/ci.yml/badge.svg)](https://github.com/akivajp/dnsmasq-webconf/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dnsmasq-webconf)](https://pypi.org/project/dnsmasq-webconf/)
[![Python](https://img.shields.io/pypi/pyversions/dnsmasq-webconf)](https://pypi.org/project/dnsmasq-webconf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A small web UI for managing **static DHCP reservations** in dnsmasq — without taking
ownership of your config file.

[日本語版 README はこちら](README.ja.md)

![Screenshot](https://raw.githubusercontent.com/akivajp/dnsmasq-webconf/master/docs/screenshot.png)

## Why this exists

Most dnsmasq front-ends (Pi-hole, router firmwares, config generators) **own** the
configuration: they generate `dnsmasq.conf` from their own database, and your
hand-written directives either get moved somewhere else or are lost.

`dnsmasq-webconf` does the opposite. It reads your existing config file, and when you
save, it **rewrites only the `dhcp-host=` lines you actually changed**. Every other
line — your `dhcp-range=`, your `dhcp-option=`, your comments, your ordering — is
preserved byte-for-byte.

That makes it useful in one specific situation: you already run dnsmasq, you want to
keep managing it as a text file, but you'd rather not hand-edit MAC addresses every
time a new device shows up.

If you want an all-in-one DNS/DHCP/ad-blocking appliance, use
[Pi-hole](https://pi-hole.net/) instead — it is far more capable, and this tool does
not try to compete with it. See [Alternatives](#alternatives) below.

## Features

- **Static DHCP reservations** — add, edit, reorder, comment out, and delete
  `dhcp-host=` entries.
- **One-click reservation from a live lease** — see a device in the DHCP lease table,
  press *Add Static*, and it becomes a fixed reservation.
- **Blocklist management** — mark a MAC as `ignore` so dnsmasq refuses to serve it.
- **Per-host notes** — comments are round-tripped as `#` comments in the config file,
  so they stay readable when you edit by hand.
- **Read-only views** of the DHCP lease table and the system `hosts` file.
- **Optional reload hook** — run `systemctl reload dnsmasq` (or anything else) after a
  successful save.
- **Pre-save validation** — run `dnsmasq --test` against the staged result before it
  touches your config; a rejected save changes nothing.
- **Live lease table** — the lease view refreshes periodically, so a newly connected
  device can be reserved the moment it appears.
- **No database, no build step, no JavaScript toolchain.** Two Python dependencies,
  and all CSS/JS is bundled — it works on an isolated network with no internet access.

## Installation

### From PyPI (recommended)

```shell
pipx install dnsmasq-webconf
# or: uv tool install dnsmasq-webconf
# or, inside a virtualenv: pip install dnsmasq-webconf
```

> On modern Debian/Ubuntu/Raspberry Pi OS, `pip install --user` is blocked by
> [PEP 668](https://peps.python.org/pep-0668/). Use `pipx` or `uv tool` instead.

### With Docker

Pre-built images are published to GHCR:

```shell
docker run --rm -p 8080:8080 \
    --user "$(id -u):$(id -g)" \
    -e DNSMASQ_WEBCONF_AUTH='admin:secret' \
    -v /etc/dnsmasq.more.conf:/etc/dnsmasq.more.conf \
    -v /var/lib/misc/dnsmasq.leases:/var/lib/misc/dnsmasq.leases:ro \
    ghcr.io/akivajp/dnsmasq-webconf:latest
```

Or build it yourself:

```shell
docker build -t dnsmasq-webconf .
docker run --rm -p 8080:8080 \
    --user "$(id -u):$(id -g)" \
    -e DNSMASQ_WEBCONF_AUTH='admin:secret' \
    -v /etc/dnsmasq.more.conf:/etc/dnsmasq.more.conf \
    -v /var/lib/misc/dnsmasq.leases:/var/lib/misc/dnsmasq.leases:ro \
    dnsmasq-webconf
```

### From source

```shell
git clone https://github.com/akivajp/dnsmasq-webconf.git
cd dnsmasq-webconf
pip install -e .
```

## Usage

```shell
dnsmasq-webconf --config /etc/dnsmasq.more.conf
```

Then open <http://127.0.0.1:8080>.

By default the server listens on **loopback only**. To expose it on your LAN you must
also set credentials:

```shell
dnsmasq-webconf --host 0.0.0.0 --auth admin:secret \
    --config /etc/dnsmasq.more.conf \
    --test-command 'dnsmasq --test -C "{path}"' \
    --reload "systemctl reload dnsmasq"
```

`--test-command` validates the staged config with dnsmasq itself before it is put in
place: `{path}` is replaced with the file being validated (quote it as shown), and a
non-zero exit status rejects the save, leaving your existing config untouched. It is
strongly recommended whenever `--reload-command` is used, so a typo in a MAC address
cannot reach a running dnsmasq.

The lease table auto-refreshes every 30 seconds by default; set
`--refresh-interval 0` to disable.

To avoid putting the password in your shell history or in `ps` output, use the
environment variable instead of `--auth`, or pass only the username — you will be
prompted for the password on the terminal:

```shell
DNSMASQ_WEBCONF_AUTH='admin:secret' dnsmasq-webconf --host 0.0.0.0 ...
# or:
dnsmasq-webconf --host 0.0.0.0 --auth admin --config /etc/dnsmasq.more.conf
```

By default the server uses Python's built-in `wsgiref` (single-threaded). Install the
optional `server` extra for a threaded server so long-running reload commands don't
stall the UI:

```shell
pipx install dnsmasq-webconf[server]
```

Run `dnsmasq-webconf --help` for the full option list.

### Recommended dnsmasq setup

Point the tool at a dedicated include file rather than your main `dnsmasq.conf`, so
that a mistake can never take down DNS resolution:

```conf
# /etc/dnsmasq.conf
conf-file=/etc/dnsmasq.more.conf
```

```shell
sudo touch /etc/dnsmasq.more.conf
dnsmasq-webconf --config /etc/dnsmasq.more.conf
```

### Running as a service

```ini
# /etc/systemd/system/dnsmasq-webconf.service
[Unit]
Description=Dnsmasq WebConf
After=network.target

[Service]
# Needs write access to the config file; adjust to suit your setup.
User=root
Environment=DNSMASQ_WEBCONF_AUTH=admin:secret
ExecStart=/usr/local/bin/dnsmasq-webconf 8080 \
    --host 0.0.0.0 \
    --config /etc/dnsmasq.more.conf \
    --reload "systemctl reload dnsmasq"
# Optional hardening: make everything outside the config file read-only
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=/etc/dnsmasq.more.conf
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Security

This tool edits the configuration of your network's DHCP server. Anyone who can write
to it can hand out arbitrary IP addresses, gateways and DNS servers to every device on
your LAN. Please treat access to it accordingly.

- **Authentication is off by default, and so is network exposure.** The server binds to
  `127.0.0.1` unless you pass `--host`. Binding to a non-loopback address without
  `--auth` is refused outright (override with `--allow-no-auth` only if you know the
  port is protected some other way).
- **HTTP Basic auth is not encrypted.** On an untrusted network, put the tool behind a
  reverse proxy with TLS, or reach it over a VPN / SSH tunnel:
  `ssh -L 8080:127.0.0.1:8080 your-server`.
- **Writes are CSRF-protected** by an `Origin` check, because browsers attach Basic
  auth credentials automatically.
- **`--reload-command` runs through a shell.** It is only ever the string you supply on
  the command line, but do not build it from untrusted input.
- Use `--read-only` if you only want the dashboard views.

### Reporting a vulnerability

Please open a [security advisory](https://github.com/akivajp/dnsmasq-webconf/security/advisories/new)
rather than a public issue.

> **Upgrading from v0.1.x?** The default listen address changed from `0.0.0.0` to
> `127.0.0.1`, and v0.1.x had **no authentication at all** on its save endpoint. If you
> ran it on a LAN, assume the config was writable by anyone who could reach the port.
> See [CHANGELOG.md](CHANGELOG.md).

## How saving works

1. The browser sends back only the entries you touched, each tagged with the line
   number it came from and the **original text of that line**.
2. The server re-reads the config file and, for each changed entry, checks that the
   line still matches what the browser saw.
3. If it matches, that single line is replaced. If it doesn't — because you edited the
   file by hand, or another session saved first — the write is **skipped and reported**
   back to you rather than silently overwriting.
4. The result is written to a temporary file in the same directory and moved into place
   with `os.replace()`, so an interrupted write cannot corrupt your config. The previous
   contents are kept as `<config>.bak` (disable with `--no-backup`).

Deleted entries are written as `##dhcp-host=...` rather than being removed, so you can
always recover them by hand.

## Scope and limitations

This tool deliberately covers a small surface. It **only** parses and edits
`dhcp-host=` directives.

Not supported: `dhcp-range`, `dhcp-option`, DNS records (`address=`, `cname=`), IPv6
reservations, query logs, statistics, and multi-user access control. Lines it does not
understand are preserved untouched, so you can manage them by hand alongside it.

## Alternatives

| Project | Best for |
|---|---|
| [Pi-hole](https://pi-hole.net/) | An all-in-one DNS/DHCP appliance with ad-blocking. Note that since v6 it generates `dnsmasq.conf` itself and does not read hand-written ones. |
| [OpenWrt LuCI](https://openwrt.org/docs/guide-user/luci/luci.essentials) / pfSense / OPNsense | You're already running a router OS — it's built in. |
| [dnsmasq-manager](https://github.com/gringolito/dnsmasq-manager) | A REST API (no UI) for scripting static leases, with JWT auth and distro packages. |
| [dnsmasq-leases-ui](https://github.com/fschlag/dnsmasq-leases-ui) | Just viewing the lease table, in a container. |
| [nexus-dnsmasq-mgr](https://github.com/brainchillz/nexus-dnsmasq-mgr) | A much broader feature set (DNS overrides, PXE boot, `dnsmasq --test` validation). |

## Development

```shell
git clone https://github.com/akivajp/dnsmasq-webconf.git
cd dnsmasq-webconf
uv venv && uv pip install -e '.[dev]'
uv run pytest
```

To refresh the bundled jQuery/Bootstrap files and their checksum manifest:

```shell
scripts/update-vendor.sh
```

Contributions are welcome — please keep the "don't rewrite lines the user didn't
change" guarantee intact, and add a test for it.

## License

MIT — see [LICENSE](LICENSE).

Bundled third-party assets (jQuery, Bootstrap) are MIT licensed; see
[`dnsmasq_webconf/static/vendor/MANIFEST.txt`](dnsmasq_webconf/static/vendor/MANIFEST.txt)
for their versions, origins and checksums.
