# Contributing

Thanks for helping. This project sends messages to real people, so **safety and consent come first**.

## Ground rules
- No change may weaken consent checks, SAFE_MODE defaults, the live-sending gate, or webhook signature verification.
- Never commit secrets, phone numbers, contact lists, databases, or screenshots showing them. Use obviously fake data (e.g. `+15550100000`).
- Do not add unofficial WhatsApp automation (WhatsApp Web scraping, bulk-sender tricks). Only the official Cloud API.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```
Optional for browser tests: `pip install playwright && playwright install chromium`.

## Workflow
1. Fork, branch from `main` (`fix/...` or `feat/...`).
2. Add or update tests; `python -m pytest -q` must pass. Tests must not touch the network or a real database (see `conftest.py`).
3. Keep changes focused; update docs when behaviour changes.
4. Open a pull request describing the change and how you tested it.

## Style
Python 3.11+, type hints where helpful, small functions, clear error messages. Never log tokens or full phone numbers.

## Reporting security issues
Do not open a public issue. See [SECURITY.md](SECURITY.md).
