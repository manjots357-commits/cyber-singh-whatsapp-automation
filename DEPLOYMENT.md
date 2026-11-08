# Production deployment

Run it somewhere always-on so Meta can reach `/webhook` over HTTPS.

## Option A - your PC + tunnel (testing)
`start_dashboard.bat`, then `ngrok http 8000`. Use the https address as the webhook URL. Set `DASHBOARD_PASSWORD`
because the tunnel is public (the app answers dashboard requests on non-local hostnames only when a password is set).
The tunnel address changes each run on the free plan, so re-save it in Meta.

## Option B - VPS / cloud VM with Docker
1. Copy the folder (without `.env`, `*.db`) to the server; create `.env` there (strong `DASHBOARD_PASSWORD`,
   `META_APP_SECRET`, `WEBHOOK_VERIFY_TOKEN`, `SAFE_MODE=true` first).
2. `docker compose up -d --build` (listens on 127.0.0.1:8000; data in the `wa_data` volume).
3. Put HTTPS in front, e.g. Caddy: `your.domain.com { reverse_proxy 127.0.0.1:8000 }`.
4. Callback URL in Meta: `https://your.domain.com/webhook`.
5. Back up the volume (`/data/whatsapp_automation.db`) regularly - it holds consent records.

## Checklist before going live
* `SAFE_MODE=false` only after a successful "send test to yourself".
* `DASHBOARD_PASSWORD` set; `.env` never committed or shared; rotate the token if it leaks.
* Single worker process only (one campaign at a time is enforced in-process).
