# Security Policy

## Reporting a vulnerability

Do not open a public issue containing credentials, account identifiers, device secrets, health data, pairing files, tunnel tokens, private hostnames, screenshots, or exploit details that expose a deployed user.

Report a suspected vulnerability privately to the project maintainers. Include the affected version, impact, reproduction steps using synthetic data, and any proposed mitigation. Remove all real physiological measurements and personal identifiers before sharing logs.

## Supported release

The production pilot supports the latest tagged release. Security fixes may require upgrading the Windows service and both matching Android companions.

## Security boundaries

Personal State is designed for read-only physiological context. It must not be used to automate treatment, replace official device alerts, or expose the loopback dashboard directly to the internet. Remote access requires an authenticated tunnel, least-privilege policy, and a separate ingest surface for device uploads.

Secrets belong in platform credential stores. Release signing keys, passwords, API tokens, real databases, exports, and pairing files must never be committed or included in release archives.
