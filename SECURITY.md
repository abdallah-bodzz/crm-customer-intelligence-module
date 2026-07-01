# Security Policy

## Supported versions

This is a portfolio / research project, not a commercial product. The `main` branch is the only maintained version.

| Version | Supported |
|---------|-----------|
| 1.x (main) | ✅ |
| Older branches | ❌ |

## Scope

Security concerns relevant to this project include:

- **Credential exposure** — hardcoded passwords or connection strings in committed files
- **SQL injection** — parameterised queries are used throughout (`sqlalchemy.text()` with named bind parameters); a bypass would be a real issue
- **Dependency vulnerabilities** — known CVEs in `requirements.txt` dependencies

Out of scope: the Olist dataset itself (publicly available on Kaggle, contains no PII).

## Reporting a vulnerability

If you find something — particularly a committed credential or an injection vector — please open a **private security advisory** on GitHub rather than a public issue:

1. Go to the repository → **Security** tab → **Advisories** → **Report a vulnerability**
2. Describe what you found and how to reproduce it
3. You'll get a response within 7 days

There is no bug bounty program. Credit in the changelog is offered for confirmed, non-trivial findings.

## Local setup notes

- `.env` is in `.gitignore` and must never be committed
- `.env.example` shows the required variable names with no real values
- Model artifacts (`python/models/`) are in `.gitignore` — they may contain training-data statistics but no PII
- Raw CSV data (`data/raw/`) is in `.gitignore` — download directly from Kaggle
