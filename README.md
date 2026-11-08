# CYBER SINGH AI — WhatsApp Campaign Automation

A consent-first campaign manager for the **official Meta WhatsApp Cloud API**. Paste a link, review the
generated message, choose people who agreed to hear from you, approve, and watch delivery and read reports.
Built with FastAPI and SQLite. It runs in **simulation mode by default**: nothing reaches Meta until you
deliberately switch live sending on.

> **Not affiliated with Meta or WhatsApp.** You are responsible for complying with the
> [WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy) and all applicable laws
> (consent, anti-spam, data protection). See [docs/MESSAGING_COMPLIANCE.md](docs/MESSAGING_COMPLIANCE.md).

## Features
- **URL to campaign:** paste a link; the app reads its title/description (SSRF-hardened fetch) and drafts Punjabi and English messages, as free text or as an approved-template proposal.
- **Approved templates:** create templates on Meta from the dashboard, sync their approval status, send with up to three variables (`{{1}}` name, `{{2}}` link, `{{3}}` title).
- **Contacts and consent:** CSV import (everyone is imported as *not opted in*), recorded opt-in with a basis, per-send consent re-check, automatic STOP / START handling, audit log.
- **Preview and approval:** exact recipient list, skipped contacts with reasons, and an explicit approval step before every campaign.
- **Safe dispatch:** duplicate prevention per (number, link), daily unique-recipient cap, retries with back-off, error classification, circuit breaker.
- **Controls:** pause, resume, stop; interrupted campaigns recover safely.
- **Reports:** delivered/read via signed webhooks (`X-Hub-Signature-256`), analytics and campaign history.
- **Simulation mode:** the default. Live sending needs credentials, `SAFE_MODE=false`, a real test message confirmed **delivered**, and typed owner authorisation.

## Requirements
- Python 3.11+
- A Meta developer app with the WhatsApp product, a WhatsApp Business Account and a sender number (only for live sending)
- A public HTTPS URL for the webhook (only for live delivery reports; a tunnel works for testing)

## Installation
**Windows:** run `setup_windows.bat`, edit `.env`, then run `start_dashboard.bat` (opens http://localhost:8000).

**Any OS:**
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then edit .env
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```
Docker: `docker compose up -d --build` (see [DEPLOYMENT.md](DEPLOYMENT.md)).

## Configuration
All settings live in `.env` (copy of [`.env.example`](.env.example); `.env` is git-ignored). Secrets are never sent to the browser or logged.

| Variable | Purpose |
|---|---|
| `SAFE_MODE` | `true` (default) = simulate only. Set `false` only when you intend to send for real. |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_BUSINESS_ACCOUNT_ID` | Meta credentials |
| `META_APP_SECRET`, `WEBHOOK_VERIFY_TOKEN` | Webhook verification (unsigned webhooks are rejected) |
| `OWNER_WHATSAPP_NUMBER` | The only number the "send test" button may message |
| `DASHBOARD_PASSWORD` | Required when exposing the dashboard beyond this computer |
| `DAILY_RECIPIENT_LIMIT` | Unique recipients per rolling 24h (250 for unverified businesses) |
| `META_GRAPH_VERSION` | Graph API version (default `v26.0`) |

## Meta WhatsApp Cloud API setup
Step-by-step screens: [META_SETUP.md](META_SETUP.md): choose a WhatsApp Business Account, business verification, sender number, system-user token, webhook, template, and the four live-sending gates.

## Dashboard usage
Short version in [OPERATING_PROCEDURE.md](OPERATING_PROCEDURE.md): paste a link, **Analyse link**, choose message and people, **Preview**, **Approve**, watch progress (Pause/Resume/Stop), read the report.

## Consent management
- Imports never grant consent. Record opt-in only for people who actually agreed, with how they agreed.
- Only contacts that are opted in, consent-verified and not opted out are ever selectable; this is re-checked immediately before each message.
- A reply of STOP / UNSUBSCRIBE (including ਬੰਦ / बंद) opts a person out instantly; START opts back in.

## Simulation mode
With `SAFE_MODE=true` (or without credentials, or before live sending is authorised) every send is **simulated**: recorded as `SIMULATED`, never counted as a real send, never contacting Meta. The dashboard shows the current mode.

## Testing
```bash
python -m pytest -q       # or run_tests.bat on Windows
```
Tests use a temporary database and pinned environment: they never read your `.env`, touch your data, or contact Meta. Browser tests are skipped when Playwright/Chromium is not installed.

## Safety limitations
- Live sending is **unproven until you** deliver a real approved test message to yourself; the software does not claim otherwise.
- Meta decides template approval, quality rating, tiers, and may refuse marketing messages per user. The app cannot override this.
- One campaign runs at a time in a single process; use one worker.
- Consent records are only as truthful as what the operator enters. You are responsible for lawful consent.
- No unofficial WhatsApp Web automation is included or supported.

## Contributing, security, license
[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [MIT License](LICENSE)
