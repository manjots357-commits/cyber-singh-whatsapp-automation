# CYBER SINGH AI — WhatsApp Campaign Automation

**A consent-first WhatsApp campaign management dashboard powered by the official Meta WhatsApp Cloud API.**

Build, review, approve, and monitor WhatsApp campaigns through a structured dashboard. Import contacts, manage consent records, prepare multilingual messages, preview recipients, and track campaign activity.

Built with **Python, FastAPI, and SQLite**.

> **Safety first:** The application runs in simulation mode by default. No real WhatsApp messages are sent unless the required credentials, configuration, test delivery, and explicit owner authorization are completed.

---

## Project Overview

CYBER SINGH AI — WhatsApp Campaign Automation is designed to help businesses manage WhatsApp campaigns responsibly.

The application provides a workflow for:

1. Importing and managing contacts.
2. Recording consent and opt-in information.
3. Creating campaigns from URLs.
4. Preparing Punjabi and English messages.
5. Reviewing recipients and skipped contacts.
6. Approving campaigns before dispatch.
7. Monitoring campaign progress and reports.

**The goal is simple: campaign management with transparency, consent, and safety controls.**

## Key Features

### 1. URL-to-Campaign Generation
- Paste a website or content URL.
- Extract page title and description.
- Generate Punjabi and English message drafts.
- Prepare free-text messages or approved-template proposals.
- Includes SSRF-hardened URL fetching.

### 2. Contact Management
- Import contacts through CSV.
- Store contact information in SQLite.
- Track opt-in and opt-out status.
- Maintain consent records and their basis.
- Imported contacts are never automatically treated as opted in.

### 3. WhatsApp Template Management
- Create WhatsApp message templates through the dashboard.
- Synchronize template approval status.
- Support up to three template variables:
  - `{{1}}` — Contact name
  - `{{2}}` — Link
  - `{{3}}` — Content title

### 4. Campaign Preview and Approval
- Review the exact recipient list.
- See contacts skipped and the reasons.
- Preview messages before sending.
- Require explicit approval before every campaign.

### 5. Safety-First Message Dispatch
- Duplicate-send prevention.
- Daily unique-recipient limits.
- Retry handling with back-off.
- Error classification.
- Circuit-breaker protection.
- Pause, resume, and stop controls.

### 6. Campaign Analytics and Reports
- Campaign history.
- Delivery and read status through signed webhooks.
- Webhook signature verification using `X-Hub-Signature-256`.
- Campaign progress and reporting.

### 7. Simulation Mode
- Enabled by default.
- Simulates message dispatch without contacting Meta.
- Displays simulated delivery status.
- Helps test campaign workflows safely.

---

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend development |
| FastAPI | Web application and API |
| SQLite | Database |
| Meta WhatsApp Cloud API | Official WhatsApp integration |
| HTML, CSS, JavaScript | Dashboard interface |
| Pytest | Automated testing |
| Docker | Containerized deployment |

---

## Application Workflow

```text
Import Contacts
      |
      v
Record Consent
      |
      v
Create Campaign
      |
      v
Generate Message
      |
      v
Preview Recipients
      |
      v
Approve Campaign
      |
      v
Dispatch / Simulation
      |
      v
Monitor Reports
```

---

## Installation

### Requirements

- Python 3.11 or newer
- Windows, Linux, or macOS
- Meta Developer App with WhatsApp product (for live integration)
- WhatsApp Business Account and sender number (for live integration)

### Windows Installation

1. Download or clone this repository.
2. Open the project folder.
3. Run:

```bat
setup_windows.bat
```

4. Configure your environment variables in `.env`.
5. Start the dashboard:

```bat
start_dashboard.bat
```

6. Open your browser:

```text
http://localhost:8000
```

### Manual Installation

```bash
python -m venv .venv
```

Activate the environment:

**Windows:**

```bat
.venv\Scripts\activate
```

**Linux / macOS:**

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your environment file:

**Windows:**

```bat
copy .env.example .env
```

**Linux / macOS:**

```bash
cp .env.example .env
```

Start the application:

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

---

## Configuration

Application settings are stored in `.env`.

| Variable | Description |
|---|---|
| `SAFE_MODE` | Simulation mode; enabled by default |
| `WHATSAPP_TOKEN` | Meta access token |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp sender number ID |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | WhatsApp Business Account ID |
| `META_APP_SECRET` | Meta app secret |
| `WEBHOOK_VERIFY_TOKEN` | Webhook verification token |
| `OWNER_WHATSAPP_NUMBER` | Authorized test recipient |
| `DASHBOARD_PASSWORD` | Dashboard password |
| `DAILY_RECIPIENT_LIMIT` | Daily unique-recipient limit |
| `META_GRAPH_VERSION` | Meta Graph API version |

**Important:** Never upload your `.env` file, access tokens, database, or private contact data to GitHub.

---

## Meta WhatsApp Cloud API Setup

For live integration, configure:

1. Meta Developer App.
2. WhatsApp product.
3. WhatsApp Business Account.
4. Sender phone number.
5. System-user access token.
6. Webhook configuration.
7. Approved message templates.
8. Required live-sending safety checks.

Read the detailed guide:

- [Meta Setup Guide](META_SETUP.md)
- [Deployment Guide](DEPLOYMENT.md)
- [Messaging Compliance](docs/MESSAGING_COMPLIANCE.md)

---

## Consent and Messaging Compliance

This project follows a consent-first approach.

- Importing a contact does not grant WhatsApp marketing consent.
- Only eligible, consent-verified contacts can be selected.
- Consent is checked again before each message.
- STOP / UNSUBSCRIBE replies trigger opt-out handling.
- START replies can restore opt-in status.
- Approved templates may be required for business-initiated messages outside the customer service window.

Users and operators are responsible for obtaining valid consent and complying with applicable laws and Meta policies.

Official policy: [WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy)

> This project is independent and is not affiliated with, endorsed by, or sponsored by Meta or WhatsApp.

---

## Safety and Live-Sending Requirements

Live sending is disabled by default.

Before enabling live sending, the required safeguards include:

- Valid Meta credentials.
- Correct WhatsApp Business configuration.
- `SAFE_MODE=false` set deliberately.
- A real test message successfully delivered to the authorized owner number.
- Explicit typed owner authorization.

A successful simulation does not prove that real WhatsApp delivery works.

Meta controls template approvals, messaging limits, quality ratings, and account eligibility. This application cannot override Meta's decisions.

---

## Testing

Run the automated test suite:

```bash
python -m pytest -q
```

Or