# Operating procedure: link -> campaign -> approve -> send -> report

## One-time (you)
1. Run `setup_windows.bat`, then fill `.env` (see META_SETUP.md). Set `META_GRAPH_VERSION=v26.0`.
2. Import contacts, record opt-in only for people who agreed (Contacts tab).
3. Get a template APPROVED, send a test to yourself, wait for DELIVERED, authorise live sending (META_SETUP step 6).
   Until then everything runs as SIMULATION - safe to practise.

## Every campaign
1. Double-click `start_dashboard.bat` (http://localhost:8000).
2. **Paste the link** in New campaign, press **Analyse link**. Title and suggested messages appear.
3. Choose the message/template and the groups (only opted-in people are listed).
4. **Preview**: check text, recipient count, what is skipped and why.
5. **Approve**: tick the box (live: type the recipient count). Nothing is sent before this.
6. Watch progress. **Pause / Resume / Stop** are available any time.
7. **Analytics / History**: sent, delivered, read, failed, opt-outs.

## Rules the system keeps
Only opted-in, not-STOPped contacts; no repeat of the same link to a number; 250/day cap until Meta verifies you;
secrets never shown; SIMULATED rows are labelled and never counted as sent.
