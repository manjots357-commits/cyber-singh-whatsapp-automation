# Meta setup guide - exact screens

Your Meta Business portfolio may already list one or more WhatsApp Business Accounts (WABAs) plus a
"Test Messaging Account". Meta limits how many WABAs an UNVERIFIED portfolio may create, so **reuse one that
exists** instead of creating another. Never delete an existing one.

## 1. Pick the WABA to reuse
business.facebook.com -> Settings (gear) -> Accounts -> **WhatsApp accounts**. Open each one and note:
phone number(s) attached, display-name status, whether it belongs to your developer app. Choose the one with
your real number attached (or the "cyber singh" one). Copy its **WhatsApp Business Account ID** into `.env`
(`WHATSAPP_BUSINESS_ACCOUNT_ID`).

## 2. Business verification (removes the WABA limit, raises your sending tier)
Settings -> Security Centre -> **Start verification** (needs business documents). Until it is done you can
message at most 250 unique people per rolling 24h and cannot create extra WABAs.

## 3. The sender phone number
Settings -> WhatsApp accounts -> your WABA -> **Phone numbers** -> Add. The number must receive an SMS/voice OTP.
A number that is already registered in the regular WhatsApp / WhatsApp Business app must normally be removed from
that app first (Meta's "coexistence" lets an app number also use the API, but it is only offered through Meta
Tech Providers). Using a fresh SIM for the API is the simplest. Then approve the display name
(WhatsApp Manager -> Phone numbers). **OTP and display-name approval can only be done by you.**

## 4. Token, IDs, app secret
* developers.facebook.com -> My Apps -> your app -> WhatsApp -> **API Setup**: copy *Phone number ID* -> `.env`.
* business.facebook.com -> Settings -> Users -> **System users** -> create one (Admin) -> assign your WhatsApp
  account + app with full control -> **Generate token** with `whatsapp_business_messaging` and
  `whatsapp_business_management` -> `.env` `WHATSAPP_TOKEN`. (The API-Setup temporary token expires in 24h.)
* App settings -> Basic -> **App secret** -> `.env` `META_APP_SECRET`.
* Add a payment method: WhatsApp Manager -> Payment settings (marketing templates are billed per message).

## 5. Webhook (delivery receipts + STOP replies)
Needs a public HTTPS address (see DEPLOYMENT.md; for testing: `ngrok http 8000`).
App -> WhatsApp -> **Configuration** -> Callback URL `https://YOUR-HOST/webhook`, Verify token = your
`WEBHOOK_VERIFY_TOKEN` -> Verify -> subscribe to **messages** and **message_template_status_update**.
Then press *Subscribe webhook* on the dashboard's Setup tab.

## 6. Template, then go live (4 gates - the system enforces them)
1. Dashboard -> Templates -> create the suggested template (or press *Create this template* after *Analyse link*) -> wait for APPROVED -> *Sync from Meta*.
2. In `.env` set `SAFE_MODE=false` and `OWNER_WHATSAPP_NUMBER=` your own number, restart.
3. Templates -> *Send test to yourself*. Wait until Meta's webhook reports it **DELIVERED**.
4. Setup tab -> *Live sending* -> type `ENABLE LIVE SENDING` -> Authorise.
Until all four are green the system stays in SIMULATION and nothing reaches Meta. *Switch live sending off* undoes step 4 at any time.
Templates may use 3 variables: {{1}} name, {{2}} link, {{3}} video title.

## Who must do what
You: business verification, OTP, display-name approval, payment method, token/secret copy, webhook URL.
The dashboard's Setup tab shows a live checklist of what is still missing.
