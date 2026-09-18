# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-18

The first maintenance release since 2024. It focuses on making the existing feature
set safe and installable rather than on adding functionality.

### Security

- **Added authentication.** The save endpoint previously had none: anyone who could
  reach the port could rewrite DHCP reservations and, if `--reload-command` was
  configured, trigger a dnsmasq reload. Use `--auth USER:PASSWORD` or the
  `DNSMASQ_WEBCONF_AUTH` environment variable.
- **Changed the default listen address from `0.0.0.0` to `127.0.0.1`.** Binding to a
  non-loopback address without credentials is now refused unless `--allow-no-auth` is
  passed explicitly. **This is a breaking change** — see *Upgrading* below.
- **Fixed a cross-site scripting vulnerability.** Lease and config data were embedded
  into a `<script>` block unescaped, so a value containing `</script>` could execute
  arbitrary JavaScript. Since DHCP clients supply their own hostnames, this was
  reachable by any device on the network. Embedded JSON is now escaped, and Jinja2
  autoescaping is enabled.
- **Fixed an XSS in the table renderer**, which passed config-file values through
  jQuery `.html()`.
- **Restricted `/api/save` to POST** and added an `Origin` check. It previously
  accepted `GET`, making it trivially exploitable via CSRF; Basic auth alone does not
  help here because browsers attach credentials automatically.
- **Upgraded bundled jQuery to 3.7.1** (from 3.4.1, affected by CVE-2020-11022 and
  CVE-2020-11023) and Bootstrap to 4.6.2.
- **Debug mode is now opt-in** via `--debug`. Debug logging, tracebacks and the
  auto-reloader were previously always enabled.

### Added

- Published to PyPI: `pipx install dnsmasq-webconf`, with a `dnsmasq-webconf` command.
- `LICENSE` (MIT). The project previously had no license, which left its reuse legally
  ambiguous despite being forked.
- `--read-only` mode, which disables the save endpoint.
- `--host`, `--version`, `--no-backup` and `--allow-no-auth` options.
- Atomic saves: writes go to a temporary file and are moved into place, so an
  interrupted write can no longer corrupt the config. The previous contents are kept
  as `<config>.bak`.
- Save results are now reported in the UI, including per-entry conflicts.
- A test suite (76 tests) and CI across Python 3.9–3.13.
- A Dockerfile and a Japanese README (`README.ja.md`).

### Fixed

- **Conflicting writes are no longer silently discarded.** When the config file had
  changed on disk, the entry was skipped but the UI still reported success. Conflicts
  are now surfaced to the user and the entry is left editable.
- **The UI no longer breaks when a target file is missing.** `config` was set to
  `null`, causing a `TypeError` that stopped all rendering; separately, the template's
  `{% if %}` guards tested the string `"null"` and were always true, so empty sections
  were rendered anyway.
- CSS and JavaScript are bundled instead of loaded from CDNs, so the UI works on
  isolated networks. jQuery was also being loaded over plain `http://`.
- A malformed timestamp in the leases file no longer causes a server error.
- Config files containing invalid UTF-8 are read with replacement instead of failing.
- `bottle.run()` was called with a misspelled `quite=` keyword instead of `quiet=`.
- `num_lines` was not reported for an empty config file.

### Changed

- The code is now an installable package (`dnsmasq_webconf/`) with parsing logic
  separated into a dependency-free module.
- `app/index.py` remains as a compatibility shim, so existing service units that
  invoke it keep working.

### Upgrading from 0.1.x

1. **Assume your config was writable by anyone who could reach the port.** Review
   your `dhcp-host=` entries for anything you did not add.
2. The server now listens on `127.0.0.1`. To restore LAN access, add
   `--host 0.0.0.0` **and** `--auth USER:PASSWORD`.
3. `python app/index.py` still works, but `dnsmasq-webconf` is preferred.

## [0.1.0] - 2019-12-07

Initial release.
