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
import os
import json
import logging
from datetime import datetime, timezone

import requests

# ─────────────────────────────────────────────
# CONFIGURATION — reads from HA add-on options
# ─────────────────────────────────────────────

OPTIONS_FILE = "/data/options.json"

try:
    with open(OPTIONS_FILE) as f:
        options = json.load(f)
except Exception as e:
    options = {}
    print(f"Failed to load options: {e}")

print(f"Options keys found: {list(options.keys())}")

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
LOG_LEVEL = options.get("log_level", "info").upper()

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
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
        "extract": "solidifi_subject"
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
        "extract": "solidifi_subject"
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
        "extract": "solidifi_subject"
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
    decoded = email.header.decode_header(value)
    parts = []
    for part, charset in decoded:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return " ".join(parts)


def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                body = part.get_payload(decode=True).decode(charset, errors="replace")
                break
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="replace")
    return body


def extract_rps_subject(subject, body):
    address = ""
    match = re.search(r'[–—-]\s*(.+?)(?:\s*$)', subject)
    if match:
        address = match.group(1).strip()
    return {"address": address, "order_id": "", "lender": "", "mortgage": "", "who_pays": "", "client_name": ""}


def extract_rps_cancelled_subject(subject, body):
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
        address = re.sub(r'\s*-\s*FULL_APPRAISAL.*$', '', parts[3]).strip()
    return {"address": address, "order_id": order_id, "lender": lender, "mortgage": "", "who_pays": "", "client_name": ""}


def extract_solidifi_subject(subject, body):
    order_id = ""
    address = ""
    match = re.search(r'(OR\d+)', subject)
    if match:
        order_id = match.group(1)
    parts = subject.split(" - ")
    if len(parts) >= 3:
        address = parts[2].strip()
    return {"address": address, "order_id": order_id, "lender": "", "mortgage": "", "who_pays": "", "client_name": ""}


def extract_nationwide_body(subject, body):
    order_id = ""
    address = ""
    match = re.search(r'NAS\s*#?\s*(\d+)', subject, re.IGNORECASE)
    if match:
        order_id = "NAS#" + match.group(1)
    match = re.search(r'Property Address[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
    if match:
        address = match.group(1).strip()
    return {"address": address, "order_id": order_id, "lender": "", "mortgage": "", "who_pays": "", "client_name": ""}


def extract_nas_special_subject(subject, body):
    order_id = ""
    address = ""
    match = re.search(r'NAS\s*#?\s*(\d+)', subject, re.IGNORECASE)
    if match:
        order_id = "NAS#" + match.group(1)
    match = re.search(r'\|\s*(?:Address\s*:\s*)?(.+?)$', subject, re.IGNORECASE)
    if match:
        address = match.group(1).strip()
    return {"address": address, "order_id": order_id, "lender": "", "mortgage": "", "who_pays": "", "client_name": ""}


def extract_alpine_body(subject, body):
    order_id = ""
    client_name = ""
    address = ""
    mortgage = ""
    lender = ""
    who_pays = ""

    match = re.search(r'#(\d+)', subject)
    if match:
        order_id = "#" + match.group(1)

    match = re.search(r'#\d+\s+(.+?)\s*-\s*Appraisal', subject)
    if match:
        client_name = match.group(1).strip()

    match = re.search(
        r'Home Email:[^\n]+\n(.+?)\n(.+?(?:ON|BC|AB|QC|MB|SK|NS|NB|PE|NL)\s+\w+\d\w+)',
        body, re.IGNORECASE | re.DOTALL
    )
    if match:
        address = match.group(1).strip() + ", " + match.group(2).strip()

    if re.search(r'1st mortgage|first mortgage', body, re.IGNORECASE):
        mortgage = "1st Mortgage"
    elif re.search(r'2nd mortgage|second mortgage', body, re.IGNORECASE):
        mortgage = "2nd Mortgage"

    if re.search(r'Our Company will pay', body, re.IGNORECASE):
        who_pays = "Co. Pays"
    elif re.search(r'The Client will pay', body, re.IGNORECASE):
        who_pays = "Client Pays"

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
    order_id = ""
    match = re.search(r'#(\d+)', subject)
    if match:
        order_id = "#" + match.group(1)
    return {"address": "", "order_id": order_id, "lender": "", "mortgage": "", "who_pays": "", "client_name": ""}


EXTRACTORS = {
    "rps_subject": extract_rps_subject,
    "rps_cancelled_subject": extract_rps_cancelled_subject,
    "solidifi_subject": extract_solidifi_subject,
    "nationwide_body": extract_nationwide_body,
    "nas_special_subject": extract_nas_special_subject,
    "alpine_body": extract_alpine_body,
    "alpine_cancelled": extract_alpine_cancelled,
}


# ─────────────────────────────────────────────
# HOME ASSISTANT INTEGRATION
# ─────────────────────────────────────────────

def send_to_ha(alert):
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }

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
    notification_title = f"{alert['icon']} {alert['label']}"
    notification_message = "\n".join(lines[1:]) if len(lines) > 1 else "Check email for details"

    # 1. Update sensor
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
        log.info(f"✅ Sensor updated: {alert['label']}")
    except Exception as e:
        log.error(f"❌ Sensor update failed: {e}")

    # 2. Fire event
    try:
        r = requests.post(
            f"{HA_URL}/api/events/appraisal_alert",
            headers=headers,
            json={
                "alert_type": alert["alert_type"],
                "label": alert["label"],
                "buzzer": alert.get("buzzer", "new_order"),
                "display_text": display_text
            },
            timeout=10
        )
        r.raise_for_status()
        log.info(f"✅ Event fired: appraisal_alert")
    except Exception as e:
        log.error(f"❌ Event fire failed: {e}")

    # 3. Phone notification
    try:
        r = requests.post(
            f"{HA_URL}/api/services/notify/mobile_app_robs_s23_ultra",
            headers=headers,
            json={
                "title": notification_title,
                "message": notification_message,
                "data": {
                    "push": {
                        "sound": "default",
                        "badge": 1
                    }
                }
            },
            timeout=10
        )
        r.raise_for_status()
        log.info(f"✅ Phone notification sent")
    except Exception as e:
        log.error(f"❌ Phone notification failed: {e}")


# ─────────────────────────────────────────────
# EMAIL MATCHING
# ─────────────────────────────────────────────

def match_rule(rule, from_addr, subject, body, account_email):
    if rule.get("account") and rule["account"] != account_email:
        return False

    from_addr = from_addr.lower()

    if rule.get("from_address"):
        if rule["from_address"].lower() not in from_addr:
            return False
    elif rule.get("from_domain"):
        if rule["from_domain"].lower() not in from_addr:
            return False

    if rule.get("subject_contains"):
        if not any(kw.lower() in subject.lower() for kw in rule["subject_contains"]):
            if not rule.get("body_contains"):
                return False

    if rule.get("body_contains"):
        if not any(kw.lower() in body.lower() for kw in rule["body_contains"]):
            return False

    return True


def process_email(msg, account_email):
    from_raw = msg.get("From", "")
    subject = decode_header_value(msg.get("Subject", ""))
    body = get_email_body(msg)

    match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', from_raw)
    from_addr = match.group(0).lower() if match else from_raw.lower()

    log.info(f"📧 FROM={from_addr} | SUBJECT={subject[:60]}")

    for rule in RULES:
        if match_rule(rule, from_addr, subject, body, account_email):
            log.info(f"✅ Matched: {rule['name']}")
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

    log.info("⏭️  No rule matched")
    return False


# ─────────────────────────────────────────────
# GMAIL IMAP
# ─────────────────────────────────────────────

def check_gmail(account):
    email_addr = account["email"]
    app_password = account["app_password"]

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_addr, app_password)
        mail.select("INBOX")

        _, data = mail.search(None, "UNSEEN")
        msg_ids = data[0].split()

        if not msg_ids:
            log.debug(f"📭 No new mail: {email_addr}")
            mail.logout()
            return

        log.info(f"📬 {len(msg_ids)} new email(s): {email_addr}")

        for msg_id in msg_ids:
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            process_email(msg, email_addr)
            mail.store(msg_id, "+FLAGS", "\\Seen")

        mail.logout()

    except imaplib.IMAP4.error as e:
        log.error(f"❌ IMAP error {email_addr}: {e}")
    except Exception as e:
        log.error(f"❌ Error {email_addr}: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log.info("🚀 Appraisal Monitor started")
    log.info(f"📧 Monitoring {len(GMAIL_ACCOUNTS)} account(s)")
    log.info(f"🔁 Poll every {POLL_INTERVAL}s")

    while True:
        for account in GMAIL_ACCOUNTS:
            check_gmail(account)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
