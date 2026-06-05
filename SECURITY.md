# Security Policy

Marksman is a non‑commercial hobby project maintained on a best‑effort basis.
There is no formal SLA, but security reports are taken seriously and will be
looked at as soon as reasonably possible.

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Report it privately via GitHub's confidential channel:

1. Go to the repository's **Security** tab →
   **[Report a vulnerability](https://github.com/adervec/MarksmanApp/security/advisories/new)**.
2. Describe the issue, the affected version/commit, and steps to reproduce.

If you can't use GitHub Security Advisories, contact the maintainer
([@adervec](https://github.com/adervec)) privately through GitHub to arrange a
disclosure channel.

Please give a reasonable window to investigate and ship a fix before any public
disclosure. Thank you for reporting responsibly.

## Supported versions

This is a single‑maintainer project; only the latest commit on the default
branch (and the most recent release, if any) receives fixes.

| Version | Supported |
|---|---|
| Latest `main` / latest release | ✅ |
| Older commits / tags | ❌ |

## Scope & threat model

Marksman runs **locally**, uses only the Python standard library at runtime, and
has **no network calls, authentication, accounts, or secrets**. The most
relevant security surface is therefore **parsing untrusted input**:

- **Images** (PNG, or other formats if optional Pillow is installed) passed to
  the `analyze` command.
- **The JSON database** (`marksman_data.json`) it loads and saves.

Reports about crashes, resource exhaustion, or unexpected code paths when
processing a malicious image or data file are in scope and welcome. As always,
exercise normal caution before running any tool on untrusted files.

*This policy is provided in good faith and is not a warranty; see
[LICENSE](LICENSE) and [DISCLAIMER.md](DISCLAIMER.md).*
