# Security policy

## Reporting a vulnerability
Please report privately through GitHub's "Report a vulnerability" (Security tab) instead of a public issue.
Include steps to reproduce and impact. Do not include real tokens or personal data.

## Handling secrets
- Secrets live only in `.env` (git-ignored). If a token or App Secret is ever exposed, revoke it in Meta Business settings immediately and generate a new one.
- The dashboard needs `DASHBOARD_PASSWORD` before it is exposed beyond localhost; terminate HTTPS in front of it.
- Webhooks are accepted only with a valid `X-Hub-Signature-256`.
