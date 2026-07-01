# Contributing

This is a solo portfolio project. External contributions aren't expected, but if you find a bug, a correctness issue, or something in the documentation that's misleading — open an issue. I'll look at it.

## What's worth raising

- **Correctness issues** — a wrong calculation, a SQL logic error, a leakage problem I missed
- **Documentation gaps** — something in the phase reports or data dictionary that's unclear or incorrect
- **Dependency security issues** — see [SECURITY.md](SECURITY.md) for the disclosure path
- **Theme feedback** — the Warm Clay and Ember Power BI themes are being submitted to the Microsoft Fabric Community Themes Gallery; feedback on those specifically is welcome

## What's not in scope

- Feature requests — this project has a defined scope and is complete
- Style preferences — the code style, naming conventions, and formatting are intentional
- "You should use X instead of Y" suggestions without a concrete correctness or performance argument

## If you do open a PR

Keep it small and focused. Include the reason, the evidence, and the fix — the same standard applied throughout the project's own bug log. A PR that follows the format in CHANGELOG.md's "Fixed" entries is the right level of detail.

## Development setup

```bash
git clone https://github.com/abdallah-bodzz/crm-customer-intelligence-module.git
cd crm-customer-intelligence-module
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your SQL Server connection details
```

Follow the setup steps in README.md to build Bronze → Silver → Gold before running any Python scripts.
