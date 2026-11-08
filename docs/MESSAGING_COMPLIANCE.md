# Messaging rules this project is built around

This is a practical summary, not legal advice. Meta's policies change; always check the current
[WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy) and Cloud API documentation.

## 1. Opt-in is required
You may message only people who clearly agreed to receive WhatsApp messages from your business, and who
can opt out at any time.
- **Imports never grant consent.** Contacts are imported as not opted in.
- Opt-in is recorded by the operator with a basis (e.g. "signed up on form") and timestamp, kept in an audit log.
- A contact is *sendable* only if: opted in **and** consent verified **and** not opted out. This is re-checked right before every message.
- Replies such as STOP / UNSUBSCRIBE (and ਬੰਦ / बंद) opt the person out immediately. The template footer tells recipients how to opt out.
- Never message people because they are in your phone book, a purchased list, or a scraped list.

## 2. The 24-hour customer service window
When a person messages you, a 24-hour window opens in which you may send free-form (non-template) messages.
Outside that window you may send **only approved template messages**.
- The app records each contact's last inbound message time and only allows free text inside the window.
- Business-initiated campaigns to people who have not written recently must use an approved template.

## 3. Approved message templates
- Templates are created in the dashboard (submitted through the Cloud API) and must be **APPROVED** by Meta before use. Sync status from the Templates tab.
- Promotional announcements (such as "new video") are normally the **MARKETING** category, which is billed per message.
- Variables use `{{1}}`, `{{2}}`, `{{3}}` and need example values at submission; variables must not contain newlines, tabs or long runs of spaces (the app cleans them). Do not start or end a body with a variable.
- Templates can be paused or disabled by Meta for low quality; the campaign engine stops on such errors.

## 4. Limits and quality
- Unverified businesses may start conversations with about 250 unique people per rolling 24 hours; higher tiers need Business Verification. `DAILY_RECIPIENT_LIMIT` enforces this and defers the rest.
- Meta may decline to deliver a marketing message to a particular user; the app records this and does not retry.
- Blocks and reports lower your quality rating. Send relevant content only to people who want it.

## 5. How the software enforces this
| Rule | Enforcement |
|---|---|
| Consent | Selectable/sendable filter + re-check per message |
| Opt-out | Webhook keyword handling, immediate and permanent until START |
| 24h window | Free text blocked outside the window; templates otherwise |
| No surprises | Preview and explicit approval before every campaign |
| No duplicates | One send per (number, link) |
| No accidental live sending | SAFE_MODE default, delivered test message, typed authorisation |
