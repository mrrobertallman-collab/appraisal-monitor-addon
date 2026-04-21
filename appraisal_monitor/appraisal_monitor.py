#!/usr/bin/env python3
"""
Appraisal Email Monitor
Monitors Gmail accounts for appraisal requests and sends alerts to Home Assistant.
Supports: RPS, Solidifi, Nationwide, Alpine Credits
"""

import imaplib
import email
import email.header
import re
import time
import json
import requests
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

# Gmail accounts to monitor
OPTIONS_FILE = "/data/options.json"
try:
    with open(OPTIONS_FILE) as f:
        options = json.load(f)
except Exception as e:
    options = {}
    print(f"Failed to load options: {e}")

GMAIL_ACCOUNTS = [
    {
        "email": options.get("gmail_account_1", "ontarioresidentialappraisal@gmail.com"),
        "app_password": options.get("app_password_1", ""),
        "label": "RPS Account"
    },
    {
        "email": options.get("gmail_account_2", "ontarioappraiser@gmail.com"),
        "app_password": options.get("app_password_2", ""),
        "label": "Main Account"
    }
]

HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = options.get("ha_token", "")
POLL_INTERVAL = int(options.get("poll_interval", 60))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/config/appraisal_monitor.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SENDER RULES
# ─────────────────────────────────────────────

RULES = [
    # ── RPS Real Solutions ──────────────────────────────────────────
    {
        "name": "RPS_SPECIAL",
        "account": "ontarioresidentialappraisal@gmail.com",
        "from_domain": "rpsrealsolutions.com",
        "from_address": "quoteinfo@rpsrealsolutions.com",
        "subject_contains": [],
        "alert_type": "urgent",
        "icon": "⚡",
        "label": "RPS SPECIAL REQUEST",
        "buzzer": "urgent",
        "extract": "rps_subject"
    },
    {
        "name": "RPS_STANDARD",
        "account": "ontarioresidentialappraisal@gmail.com",
        "from_domain": "rpsrealsolutions.com",
        "from_address": "info@rpsrealsolutions.com",
        "subject_contains": ["New Order", "Quote Approved", "Action Required"],
        "alert_type": "new_order",
        "icon": "🔔",
        "label": "RPS",
        "buzzer": "new_order",
        "extract": "rps_subject"
    },
    {
        "name": "RPS_CANCELLED",
        "account": "ontarioresidentialappraisal@gmail.com",
        "from_domain": "rpsrealsolutions.com",
        "subject_contains": ["Order Cancelled", "Cancelled"],
        "alert_type": "cancelled",
        "icon": "❌",
        "label": "CANCELLED — RPS",
        "buzzer": "cancelled",
        "extract": "rps_cancelled_subject"
    },

    # ── Solidifi ────────────────────────────────────────────────────
    {
        "name": "SOLIDIFI_NEW",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "values@solidifi.com",
        "subject_contains": ["New Appraisal Order", "Fee and/or Due Date Change Approved"],
        "alert_type": "new_order",
        "icon": "🔔",
        "label": "SOLIDIFI",
        "buzzer": "new_order",
        "extract": "solidifi_body"
    },
    {
        "name": "SOLIDIFI_CANCELLED",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "values@solidifi.com",
        "subject_contains": ["Cancelled", "Canceled"],
        "alert_type": "cancelled",
        "icon": "❌",
        "label": "CANCELLED — SOLIDIFI",
        "buzzer": "cancelled",
        "extract": "solidifi_body"
    },
    {
        "name": "SOLIDIFI_UPDATE",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "values@solidifi.com",
        "subject_contains": ["Status Update"],
        "alert_type": "update",
        "icon": "ℹ️",
        "label": "SOLIDIFI UPDATE",
        "buzzer": "update",
        "extract": "solidifi_body"
    },

    # ── Nationwide ──────────────────────────────────────────────────
    {
        "name": "NAS_NEW",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "do-not-reply@nationwideappraisals.com",
        "subject_contains": ["New Appraisal Request"],
        "alert_type": "new_order",
        "icon": "🔔",
        "label": "NATIONWIDE",
        "buzzer": "new_order",
        "extract": "nationwide_body"
    },
    {
        "name": "NAS_CANCELLED",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "do-not-reply@nationwideappraisals.com",
        "subject_contains": ["CANCELLED", "Cancelled"],
        "alert_type": "cancelled",
        "icon": "❌",
        "label": "CANCELLED — NATIONWIDE",
        "buzzer": "cancelled",
        "extract": "nationwide_body"
    },
    {
        "name": "NAS_SPECIAL_AMTEAM",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "amteam@nationwideappraisals.com",
        "subject_contains": ["Quote for NAS"],
        "alert_type": "urgent",
        "icon": "⚡",
        "label": "NATIONWIDE SPECIAL",
        "buzzer": "urgent",
        "extract": "nas_special_subject"
    },
    {
        "name": "NAS_SPECIAL_FEE",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "feeapproval@nationwideappraisals.com",
        "subject_contains": ["Quote for NAS"],
        "alert_type": "urgent",
        "icon": "⚡",
        "label": "NATIONWIDE FEE APPROVAL",
        "buzzer": "urgent",
        "extract": "nas_special_subject"
    },

    # ── Alpine Credits ───────────────────────────────────────────────
    {
        "name": "ALPINE_ORDER",
        "account": "ontarioappraiser@gmail.com",
        "from_domain": "alpinecredits.ca",
        "subject_contains": ["Appraisal Order", "Appraisal Inquiry"],
        "alert_type": "new_order",
        "icon": "🔔",
        "label": "ALPINE CREDITS",
        "buzzer": "new_order",
        "extract": "alpine_body"
    },
    {
        "name": "ALPINE_CANCELLED",
        "account": "ontarioappraiser@gmail.com",
        "from_domain": "alpinecredits.ca",
        "subject_contains": [],
        "body_contains": ["has now been cancelled", "please cancel", "cancel the request"],
        "alert_type": "cancelled",
        "icon": "❌",
        "label": "CANCELLED — ALPINE",
        "buzzer": "cancelled",
        "extract": "alpine_cancelled"
    },
]


# ─────────────────────────────────────────────
# EXTRACTION FUNCTIONS
# ─────────────────────────────────────────────

def decode_header_value(value):
    """Decode email header value."""
    decoded = email.header.decode_header(value)
    parts = []
    for part, charset in decoded:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return " ".join(parts)

def strip_html(text):
    if not text:
        return text
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&#160;', ' ')
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                body = part.get_payload(decode=True).decode(charset, errors="replace")
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    charset = part.get_content_charset() or "utf-8"
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                    body = strip_html(body)
                    break
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="replace")
        if '<' in body and '>' in body:
            body = strip_html(body)
    return body


def extract_rps_subject(subject, body):
    """Extract address from RPS subject line."""
    # Format: Action Required: New Order Available for Assignment / ... – ADDRESS
    address = ""
    match = re.search(r'[–—-]\s*(.+?)(?:\s*$)', subject)
    if match:
        address = match.group(1).strip()
    return {"address": address, "order_id": "", "lender": "", "mortgage": "", "who_pays": ""}


def extract_rps_cancelled_subject(subject, body):
    """Extract from RPS cancelled subject.
    Format: RPS Order Cancelled - RPS Order ID 5025915 - RBC - 4030 HARWOOD RD, Baltimore ON
    """
    order_id = ""
    lender = ""
    address = ""
    match = re.search(r'Order ID\s+(\d+)', subject)
    if match:
        order_id = match.group(1)
    parts = subject.split(" - ")
    if len(parts) >= 3:
        lender = parts[2].strip()
    if len(parts) >= 4:
        address = parts[3].strip()
        # Remove FULL_APPRAISAL suffix if present
        address = re.sub(r'\s*-\s*FULL_APPRAISAL.*$', '', address).strip()
    return {"address": address, "order_id": order_id, "lender": lender, "mortgage": "", "who_pays": ""}


def extract_solidifi_body(subject, body):
    """Extract from Solidifi email body."""
    order_id = ""
    address = ""
    lender = ""
    client_name = ""
    form_type = ""
    due_date = ""
    special_instructions = ""

    match = re.search(r'(OR\d+)', subject)
    if match:
        order_id = match.group(1)

    if body:
        match = re.search(r'Property Address[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            address = match.group(1).strip()

        match = re.search(r'Lender[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            lender = match.group(1).strip()

        match = re.search(r'Borrower Name[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            client_name = match.group(1).strip()

        match = re.search(r'Appraisal Form Type[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            form_type = match.group(1).strip()

        match = re.search(r'Due Date[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            due_date = match.group(1).strip()

        match = re.search(r'Special Instructions[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            if val:
                special_instructions = val

    if not address:
        parts = subject.split(" - ")
        if len(parts) >= 3:
            address = re.sub(r'\s*-?\s*OR\d+.*$', '', parts[2]).strip()

    return {
        "address": address,
        "order_id": order_id,
        "lender": lender,
        "client_name": client_name,
        "mortgage": form_type,
        "who_pays": due_date,
        "special_instructions": special_instructions,
        "cof_deadline": ""
    }


def extract_nationwide_body(subject, body):
    """Extract NAS# and address from Nationwide body."""
    order_id = ""
    address = ""
    # NAS# from subject
    match = re.search(r'NAS\s*#?\s*(\d+)', subject, re.IGNORECASE)
    if match:
        order_id = "NAS#" + match.group(1)
    # Address from body
    match = re.search(r'Property Address[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
    if match:
        address = match.group(1).strip()
    return {"address": address, "order_id": order_id, "lender": "", "mortgage": "", "who_pays": ""}


def extract_nas_special_subject(subject, body):
    """Extract from Nationwide special subject.
    Format: Quote for NAS #11500844 | 128 CIRCLE RD, Lake St Peter, ON
    """
    order_id = ""
    address = ""
    match = re.search(r'NAS\s*#?\s*(\d+)', subject, re.IGNORECASE)
    if match:
        order_id = "NAS#" + match.group(1)
    match = re.search(r'\|\s*(?:Address\s*:\s*)?(.+?)$', subject, re.IGNORECASE)
    if match:
        address = match.group(1).strip()
    return {"address": address, "order_id": order_id, "lender": "", "mortgage": "", "who_pays": ""}


def extract_alpine_body(subject, body):
    """Extract from Alpine Credits structured email body."""
    order_id = ""
    client_name = ""
    address = ""
    mortgage = ""
    lender = ""
    who_pays = ""

    # Order # from subject: Alpine Credits #462977 Barbeau, Elaine Christine
    match = re.search(r'#(\d+)', subject)
    if match:
        order_id = "#" + match.group(1)

    # Client name from subject (after order #)
    match = re.search(r'#\d+\s+(.+?)\s*-\s*Appraisal', subject)
    if match:
        client_name = match.group(1).strip()

    # Address from body — line after client phone/email line
    # Pattern: Name (Home Phone: ...; Home Email: ...)\nADDRESS\nCITY PROV POSTAL
    match = re.search(
        r'Home Email:[^\n]+\n(.+?)\n(.+?(?:ON|BC|AB|QC|MB|SK|NS|NB|PE|NL)\s+\w+\d\w+)',
        body, re.IGNORECASE | re.DOTALL
    )
    if match:
        address = match.group(1).strip() + ", " + match.group(2).strip()

    # Mortgage type
    if re.search(r'1st mortgage|first mortgage', body, re.IGNORECASE):
        mortgage = "1st Mortgage"
    elif re.search(r'2nd mortgage|second mortgage', body, re.IGNORECASE):
        mortgage = "2nd Mortgage"

    # Who pays
    if re.search(r'Our Company will pay', body, re.IGNORECASE):
        who_pays = "Co. Pays"
    elif re.search(r'The Client will pay', body, re.IGNORECASE):
        who_pays = "Client Pays"

    # Intended user / lender
    match = re.search(r'intended user to be[:\s*]+(.+?)(?:\*|\n|$)', body, re.IGNORECASE)
    if match:
        lender = match.group(1).strip().strip('*').strip()

    return {
        "address": address,
        "order_id": order_id,
        "client_name": client_name,
        "mortgage": mortgage,
        "lender": lender,
        "who_pays": who_pays
    }


def extract_alpine_cancelled(subject, body):
    """Extract from Alpine Credits cancellation reply."""
    order_id = ""
    match = re.search(r'#(\d+)', subject)
    if match:
        order_id = "#" + match.group(1)
    return {"address": "", "order_id": order_id, "lender": "", "mortgage": "", "who_pays": ""}


EXTRACTORS = {
    "rps_subject": extract_rps_subject,
    "rps_cancelled_subject": extract_rps_cancelled_subject,
    "solidifi_body": extract_solidifi_body,
    "nationwide_body": extract_nationwide_body,
    "nas_special_subject": extract_nas_special_subject,
    "alpine_body": extract_alpine_body,
    "alpine_cancelled": extract_alpine_cancelled,
}


# ─────────────────────────────────────────────
# HOME ASSISTANT INTEGRATION
# ─────────────────────────────────────────────

def send_to_ha(alert):
    """Send alert to Home Assistant as a sensor state + event."""
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }

    # Build display lines
    lines = [f"{alert['icon']} {alert['label']}"]
    if alert.get("order_id"):
        lines.append(alert["order_id"])
    if alert.get("client_name"):
        lines.append(alert["client_name"])
    if alert.get("address"):
        lines.append(alert["address"])
    if alert.get("mortgage"):
        lines.append(alert["mortgage"])
    if alert.get("who_pays"):
        lines.append(alert["who_pays"])
    if alert.get("lender"):
        lines.append(f"Lender: {alert['lender']}")

    display_text = "\n".join(lines)

    # Update HA sensor
    sensor_payload = {
        "state": alert["alert_type"],
        "attributes": {
            "friendly_name": "Appraisal Alert",
            "icon": alert["icon"],
            "label": alert["label"],
            "alert_type": alert["alert_type"],
            "order_id": alert.get("order_id", ""),
            "address": alert.get("address", ""),
            "mortgage": alert.get("mortgage", ""),
            "lender": alert.get("lender", ""),
            "who_pays": alert.get("who_pays", ""),
            "client_name": alert.get("client_name", ""),
            "buzzer": alert.get("buzzer", ""),
            "display_text": display_text,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "rule_name": alert.get("rule_name", "")
        }
    }

    try:
        r = requests.post(
            f"{HA_URL}/api/states/sensor.appraisal_alert",
            headers=headers,
            json=sensor_payload,
            timeout=10
        )
        r.raise_for_status()
        log.info(f"✅ HA sensor updated: {alert['label']}")
    except Exception as e:
        log.error(f"❌ Failed to update HA sensor: {e}")

    # Fire HA event
    event_payload = {
        "alert_type": alert["alert_type"],
        "label": alert["label"],
        "buzzer": alert.get("buzzer", "new_order"),
        "display_text": display_text
    }

    try:
        r = requests.post(
            f"{HA_URL}/api/events/appraisal_alert",
            headers=headers,
            json=event_payload,
            timeout=10
        )
        r.raise_for_status()
        log.info(f"✅ HA event fired: appraisal_alert")
    except Exception as e:
        log.error(f"❌ Failed to fire HA event: {e}")

    # 3. Append to history file
    try:
        history_file = "/config/appraisal_history.json"
        try:
            with open(history_file, "r") as f:
                history = json.load(f)
        except Exception:
            history = []

        history.insert(0, {
            "time": datetime.now(timezone.utc).isoformat(),
            "label": alert.get("label", ""),
            "order_id": alert.get("order_id", ""),
            "address": alert.get("address", ""),
            "mortgage": alert.get("mortgage", ""),
            "lender": alert.get("lender", ""),
            "client_name": alert.get("client_name", ""),
            "alert_type": alert.get("alert_type", ""),
            "special_instructions": alert.get("special_instructions", "")
        })
        history = history[:20]

        with open(history_file, "w") as f:
            json.dump(history, f)
        log.info(f"✅ History updated")
    except Exception as e:
        log.error(f"❌ History update failed: {e}")


# ─────────────────────────────────────────────
# EMAIL MATCHING
# ─────────────────────────────────────────────

def match_rule(rule, from_addr, subject, body, account_email):
    """Check if an email matches a rule."""

    # Must match account
    if rule.get("account") and rule["account"] != account_email:
        return False

    from_addr = from_addr.lower()

    # Match by exact address
    if rule.get("from_address"):
        if rule["from_address"].lower() not in from_addr:
            return False

    # Match by domain
    elif rule.get("from_domain"):
        if rule["from_domain"].lower() not in from_addr:
            return False

    # Subject keyword match (if specified, at least one must match)
    if rule.get("subject_contains"):
        if not any(kw.lower() in subject.lower() for kw in rule["subject_contains"]):
            # Check body_contains as fallback
            if not rule.get("body_contains"):
                return False

    # Body keyword match (for cancellations buried in threads)
    if rule.get("body_contains"):
        if not any(kw.lower() in body.lower() for kw in rule["body_contains"]):
            return False

    return True


def process_email(msg, account_email):
    """Process a single email against all rules."""
    from_raw = msg.get("From", "")
    subject = decode_header_value(msg.get("Subject", ""))
    body = get_email_body(msg)

    # Extract from address
    match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', from_raw)
    from_addr = match.group(0).lower() if match else from_raw.lower()

    log.info(f"📧 Processing: FROM={from_addr} | SUBJECT={subject[:60]}")

    for rule in RULES:
        if match_rule(rule, from_addr, subject, body, account_email):
            log.info(f"✅ Matched rule: {rule['name']}")

            # Extract data
            extractor = EXTRACTORS.get(rule["extract"])
            extracted = extractor(subject, body) if extractor else {}

            alert = {
                "rule_name": rule["name"],
                "icon": rule["icon"],
                "label": rule["label"],
                "alert_type": rule["alert_type"],
                "buzzer": rule["buzzer"],
                **extracted
            }

            send_to_ha(alert)
            return True

    log.info(f"⏭️  No rule matched")
    return False


# ─────────────────────────────────────────────
# GMAIL IMAP MONITOR
# ─────────────────────────────────────────────

def check_gmail(account):
    """Connect to Gmail and check for new unread emails."""
    email_addr = account["email"]
    app_password = account["app_password"]

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_addr, app_password)
        mail.select("INBOX")

        # Search for unread emails
        _, data = mail.search(None, "UNSEEN")
        msg_ids = data[0].split()

        if not msg_ids:
            log.debug(f"📭 No new mail for {email_addr}")
            mail.logout()
            return

        log.info(f"📬 {len(msg_ids)} new email(s) for {email_addr}")

        for msg_id in msg_ids:
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            matched = process_email(msg, email_addr)

            # Mark as read regardless (so we don't re-process)
            mail.store(msg_id, "+FLAGS", "\\Seen")

        mail.logout()

    except imaplib.IMAP4.error as e:
        log.error(f"❌ IMAP error for {email_addr}: {e}")
    except Exception as e:
        log.error(f"❌ Unexpected error for {email_addr}: {e}")


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────

def main():
    log.info("🚀 Appraisal Monitor started")
    log.info(f"📧 Monitoring {len(GMAIL_ACCOUNTS)} Gmail account(s)")
    log.info(f"🔁 Poll interval: {POLL_INTERVAL}s")

    while True:
        for account in GMAIL_ACCOUNTS:
            check_gmail(account)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
