# Unconventional Trading — Fulfillment Service

Without this, customers pay and get Paddle's generic receipt — nothing from you. This service listens for Paddle's "payment actually completed" webhook and automatically emails the customer their product.

## What happens for each package

| Package | What gets emailed |
|---|---|
| Bot Pack | `bot-pack.zip` (4 EAs) attached |
| Creator Strategies | `creator-strategies.zip` (3 EAs) attached |
| Strategy Lab | Instructions email — no file, since it's a feature on the site itself |
| AI Engine | `ai-engine.zip` (4 EAs) attached |

## Setup

### 1. Get your Paddle webhook secret

Paddle dashboard → **Developer Tools** → **Notifications** → click into your notification destination (create one if you haven't) → copy the secret key (starts with `pdl_ntfset_`).

**Set the destination URL to:** `https://your-deployed-url.onrender.com/webhooks/paddle` (once deployed — see step 4). Subscribe it to at least `transaction.completed`.

### 2. Get a Gmail App Password

This is NOT your normal Gmail password — Google requires a special one for apps like this.
1. Turn on 2-Step Verification on the Gmail account you want to send from, if not already on
2. Go to https://myaccount.google.com/apppasswords
3. Generate a new app password, name it something like "Unconventional Fulfillment"
4. Copy the 16-character password it gives you

### 3. Install and test locally (optional but recommended)

```bash
pip install -r requirements.txt
export PADDLE_WEBHOOK_SECRET=pdl_ntfset_...
export GMAIL_ADDRESS=youraddress@gmail.com
export GMAIL_APP_PASSWORD=the16charpassword
python app.py
```

Paddle requires a public HTTPS URL for webhooks — it can't reach `localhost` directly. To test locally before deploying, use a tunnel tool (Paddle's own docs recommend the Hookdeck CLI: `hookdeck listen 5001 paddle-source`), or just skip local testing and deploy straight to Render, then use Paddle's webhook simulator (Developer Tools → Notifications → Simulate) to send a fake completed-transaction event at your live URL.

### 4. Deploy (same pattern as the Strategy Lab backend)

1. Push this folder to its own GitHub repo
2. Render → New → Web Service → connect the repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
5. Environment variables: `PADDLE_WEBHOOK_SECRET`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
6. Deploy, grab the URL, put `https://that-url.onrender.com/webhooks/paddle` into Paddle's notification destination from step 1

### 5. Test end-to-end

Do a real sandbox test purchase on the site (like you already did). Check:
- Render logs show the webhook arrived and was processed
- The email actually arrives with the right zip attached

## Important limits to know about, honestly

- **In-memory dedupe** (`_processed_event_ids`) resets every time the server restarts or redeploys. Fine at low volume; if you scale up, swap this for a real database check so a webhook retry never double-sends.
- **Gmail's sending limit** is roughly 500 emails/day on a personal account. Fine for a while; if you outgrow it, move to a proper transactional email service (Resend, Postmark, SendGrid) instead of Gmail SMTP.
- **When you go live on Paddle** (switch from sandbox to production), you'll get new production price IDs — update the `PRICE_TO_PRODUCT` dict at the top of `app.py`, or live purchases won't match anything and nothing will get delivered.
