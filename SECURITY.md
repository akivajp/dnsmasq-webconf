# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.2.x | Yes |
| 0.1.x | No — see the security notes in [CHANGELOG.md](CHANGELOG.md) |

## Reporting a vulnerability

Please report security issues privately via
[GitHub Security Advisories](https://github.com/akivajp/dnsmasq-webconf/security/advisories/new)
rather than opening a public issue.

## Threat model

This tool edits the configuration of a network's DHCP server. Write access to it is
equivalent to control over IP address, gateway and DNS assignment for every device on
the LAN, so it should be treated as a privileged administrative interface.

Design assumptions:

- The operator is trusted. `--reload-command` is executed through a shell by design.
- The config file, the leases file and the `hosts` file are **not** fully trusted.
  Lease hostnames in particular are supplied by DHCP clients, so all values read from
  these files are escaped before being rendered.
- HTTP Basic auth provides no confidentiality. Deploy behind TLS or a VPN when the
  network is not trusted.
- There is a single set of credentials and no per-user roles or audit log.
