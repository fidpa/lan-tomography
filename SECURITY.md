# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in `lan-tomography`, please report it
responsibly:

1. **Do NOT** open a public issue.
2. **Use GitHub Security Advisories**: navigate to the
   [Security tab](https://github.com/fidpa/lan-tomography/security/advisories)
   and click "Report a vulnerability".
3. **Provide details**:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if available)

## Response Timeline

- **Initial Response**: within 72 hours
- **Status Update**: within 7 days
- **Fix Timeline**: depends on severity (critical issues prioritized)

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x.x   | :white_check_mark: (pre-1.0; latest minor only) |

Once 1.0 ships, the latest minor version of the current major plus the previous
major's last minor will be supported.

## Threat Model

These tools measure a network you are investigating. That gives them two
properties worth stating plainly.

| Boundary | Trust level |
|---|---|
| Local root on the probe host | trusted (capture, systemd units) |
| The measured network | **untrusted** — it is the object under investigation |
| SNMP community strings | secret; read-only access is sufficient and expected |
| SMB credentials for `contrib/` tooling | secret; read-only access is sufficient |
| Capture files under `LT_BASE_DIR` | **sensitive** — see below |
| systemd journal | trusted |

The tools open **no inbound network ports**. They send ICMP echo requests, open
TCP connections to configured ports, issue SNMP GET/GETBULK requests, and capture
frames on a local interface.

### Captures are the sensitive part

A packet capture contains whatever crossed the wire, including payloads. The
shipped capture filters are deliberately narrow (`stp or arp`, broadcast and
multicast counters), but a wider filter will collect user data.

- Treat `LT_BASE_DIR` as confidential. It is not a log directory, it is evidence.
- Never publish a capture from a production network. This repository ships
  **synthetic** sample data for exactly that reason.
- Where a network is under investigation on someone else's behalf, capture scope
  is usually a matter of written agreement, not a technical choice.

## Security Best Practices for Operators

- **Do not scan.** These tools probe a small, explicitly configured target list
  at a low rate. The TCP prober deliberately spaces connections (a connect to a
  Windows RDP port truncates that host's own connection log). Turning this into a
  port scanner will get you a different kind of incident.
- **Permissions**: `chmod 644` for sourced libraries, `755` for executables,
  `600` for any file holding SNMP communities or SMB credentials.
- **Secrets**: never commit them. Use the `.example` templates and point
  `LT_SECRETS` at a file outside the repository.
- **Capture privileges**: prefer file capabilities
  (`setcap cap_net_raw,cap_net_admin=eip`) over running the whole toolchain as
  root.
- **systemd hardening**: keep `ProtectSystem=strict`, `PrivateTmp=true` and
  `NoNewPrivileges=true` in the shipped units. Note that `NoNewPrivileges` and
  `ProtectHome` interact badly with interpreters installed under `/home`; the
  unit templates document where.

## Disclosure Policy

We follow responsible disclosure:

- Security issues are fixed before public disclosure.
- Credit is given to reporters (unless they prefer anonymity).
- CVE IDs are assigned for critical vulnerabilities.
