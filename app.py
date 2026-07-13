"""
app.py — Fulfillment service

Listens for Paddle webhooks and, when a payment actually completes, emails
the customer their product automatically. Without this, customers pay and
get Paddle's generic receipt email — nothing from you, nothing they can use.

Flow:
  Paddle sends a webhook -> we verify it's genuinely from Paddle (not
  forged) -> we check it's a completed transaction -> we look up which
  package(s) were purchased -> we email the customer the right file
  (or, for Strategy Lab, instructions instead of a file).

Requires:
    pip install flask paddle-billing python-dotenv

Environment variables required:
    PADDLE_WEBHOOK_SECRET   - from Paddle dashboard > Developer Tools > Notifications
                              (click into your notification destination to see it)
    GMAIL_ADDRESS           - the Gmail account sending fulfillment emails
    GMAIL_APP_PASSWORD      - an App Password (NOT your normal Gmail password) -
                              generate one at myaccount.google.com/apppasswords
                              (requires 2-Step Verification enabled on the account)
"""

import os
import smtplib
from email.message import EmailMessage
from flask import Flask, request, jsonify
from paddle_billing.Notifications import Secret, Verifier
from paddle_billing import Client, Environment, Options

app = Flask(__name__)

PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
PADDLE_API_KEY = os.environ.get("PADDLE_API_KEY", "")  # server-side secret key, NOT the client-side checkout token
PADDLE_ENV = os.environ.get("PADDLE_ENV", "sandbox")  # "sandbox" or "production" - must match what you're testing against
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

_paddle_client = None
def get_paddle_client():
    global _paddle_client
    if _paddle_client is None:
        env = Environment.SANDBOX if PADDLE_ENV == "sandbox" else Environment.PRODUCTION
        _paddle_client = Client(PADDLE_API_KEY, options=Options(env))
    return _paddle_client


def get_customer_email(data):
    """Transaction webhooks usually only include customer_id, not the email
    directly - so we look it up via the Paddle API. Some payloads DO embed
    a customer object directly, so we check that first as a shortcut."""
    embedded = (data.get("customer", {}) or {}).get("email")
    if embedded:
        return embedded

    customer_id = data.get("customer_id")
    if not customer_id:
        return None

    try:
        customer = get_paddle_client().customers.get(customer_id)
        return customer.email
    except Exception as e:
        app.logger.error(f"Failed to fetch customer {customer_id} from Paddle API: {e}")
        return None

DELIVERABLES_DIR = os.path.join(os.path.dirname(__file__), "deliverables")

# ============================================
# PRICE ID -> PRODUCT MAPPING
# These are your SANDBOX price IDs right now. When you switch Paddle to
# production, you'll get NEW price IDs for the live catalog - update this
# dict then, or fulfillment will silently fail to match real purchases.
# ============================================
PRICE_TO_PRODUCT = {
    "pri_01kxatcpy53ks1tjckrsxcyk5g": {
        "name": "Bot Pack",
        "file": "bot-pack.zip",
    },
    "pri_01kxatjtagmtanw14a7kzc9ea4": {
        "name": "Creator Strategies",
        "file": "creator-strategies.zip",
    },
    "pri_01kxatpn6xv6wgg8r3t8a96611": {
        "name": "Strategy Lab",
        "file": None,  # not a file delivery - it's a site feature, see send_email()
    },
    "pri_01kxatry86n6f04z1y8bb69d54": {
        "name": "AI Engine",
        "file": "ai-engine.zip",
    },
}

# Very basic in-memory dedupe so a retried webhook doesn't email the customer
# twice. Resets if the server restarts - fine for early volume, but if you
# outgrow this, swap for a real database/Redis check on event_id.
_processed_event_ids = set()


def send_email(to_email, product_name, deliverable_filename):
    msg = EmailMessage()
    msg["Subject"] = f"Your {product_name} — Unconventional Trading"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email

    if deliverable_filename:
        body = (
            f"Thanks for picking up {product_name}.\n\n"
            f"Your files are attached to this email. Before doing anything else:\n"
            f"1. Open each .mq5 file in MetaEditor and compile it (F7)\n"
            f"2. Run it on a DEMO account first - not live, not yet\n"
            f"3. Read the included strategy comments so you know exactly what each one does\n\n"
            f"Questions? Just reply to this email or reach us at unconventional.help.inc@gmail.com\n\n"
            f"- Unconventional Trading"
        )
    else:
        body = (
            f"Thanks for picking up {product_name}.\n\n"
            f"Strategy Lab is a feature on the site itself, not a download - head back to "
            f"the site and scroll to the Strategy Lab section to describe your strategy and "
            f"generate it.\n\n"
            f"Questions? Just reply to this email or reach us at unconventional.help.inc@gmail.com\n\n"
            f"- Unconventional Trading"
        )
    msg.set_content(body)

    if deliverable_filename:
        filepath = os.path.join(DELIVERABLES_DIR, deliverable_filename)
        if os.path.exists(filepath):
            with open(filepath, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype="application",
                    subtype="zip",
                    filename=deliverable_filename,
                )
        else:
            app.logger.error(f"Deliverable file not found: {filepath}")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


@app.route("/webhooks/paddle", methods=["POST"])
def paddle_webhook():
    raw_body = request.get_data()  # MUST be raw bytes, not parsed JSON - signature depends on exact bytes
    signature = request.headers.get("Paddle-Signature", "")

    try:
        is_valid = Verifier().verify(request, Secret(PADDLE_WEBHOOK_SECRET))
    except Exception as e:
        app.logger.error(f"Signature verification error: {e}")
        return jsonify({"error": "invalid signature"}), 400

    if not is_valid:
        app.logger.warning("Rejected webhook with invalid Paddle signature.")
        return jsonify({"error": "invalid signature"}), 400

    event = request.get_json(silent=True) or {}
    event_type = event.get("event_type", "")
    event_id = event.get("event_id", "")

    # Only act on genuinely completed payments - not "created", not "ready",
    # only actual confirmed payment. This is the difference between someone
    # starting checkout and someone actually paying you.
    if event_type not in ("transaction.completed", "transaction.paid"):
        return jsonify({"status": "ignored", "event_type": event_type}), 200

    if event_id in _processed_event_ids:
        return jsonify({"status": "already processed"}), 200

    data = event.get("data", {})
    customer_email = get_customer_email(data)
    items = data.get("items", [])

    if not customer_email:
        app.logger.error(f"No customer email found in webhook payload for event {event_id}")
        return jsonify({"error": "no customer email"}), 400

    delivered_any = False
    for item in items:
        price_id = (item.get("price", {}) or {}).get("id") or item.get("price_id")
        product = PRICE_TO_PRODUCT.get(price_id)
        if not product:
            app.logger.warning(f"Unrecognized price_id in webhook: {price_id}")
            continue
        try:
            send_email(customer_email, product["name"], product["file"])
            delivered_any = True
        except Exception as e:
            app.logger.error(f"Failed to send fulfillment email for {price_id} to {customer_email}: {e}")

    if delivered_any:
        _processed_event_ids.add(event_id)

    return jsonify({"status": "processed", "delivered": delivered_any}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
