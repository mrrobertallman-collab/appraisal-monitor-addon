#!/usr/bin/env python3
"""
Appraisal Email Monitor
Monitors Gmail accounts for appraisal requests and sends alerts to Home Assistant.
Supports: RPS, Solidifi, Nationwide, Alpine Credits
Version: 1.2
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
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/data/appraisal_monitor.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Set to True to log parsed lines and extracted fields for troubleshooting
DEBUG_MODE = str(options.get("debug_mode", "false")).lower() in ("1", "true", "yes", "on")


# ─────────────────────────────────────────────
# SENDER RULES
# ─────────────────────────────────────────────

RULES = [
    # RPS Real Solutions
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

    # Solidifi
    {
        "name": "SOLIDIFI_NEW",
        "account": "ontarioappraiser@gmail.com",
        "from_address": "values@solidifi.com",
        "subject_contains": ["New Appraisal Order", "New Order", "Fee and/or Due Date Change Approved"],
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
        "subject_contains": ["Status Update", "Order Updated", "Off Hold", "Appraisal Order Details Changed", "Notice of Appraisal Delay", "Appraisal Order Off Hold"],
        "alert_type": "update",
        "icon": "ℹ️",
        "label": "SOLIDIFI UPDATE",
        "buzzer": "update",
        "extract": "solidifi_body"
    },

    # Nationwide
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

    # Alpine Credits
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
# ORIGINAL HELPERS
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



def get_email_body(msg):
    """Return best available body. Prefer HTML for structured vendor emails."""
    plain_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                continue

            if ctype == "text/html" and not html_body:
                html_body = decoded
            elif ctype == "text/plain" and not plain_body:
                plain_body = decoded
    else:
        charset = msg.get_content_charset() or "utf-8"
        raw = msg.get_payload(decode=True)
        if raw:
            decoded = raw.decode(charset, errors="replace")
            if "<html" in decoded.lower() or "<table" in decoded.lower() or "<div" in decoded.lower():
                html_body = decoded
            else:
                plain_body = decoded

    return html_body or plain_body or ""


def html_to_lines(html):
    """Convert HTML to clean line-based text while preserving structure."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return [line for line in lines if line]


def get_line_value(lines, label, max_lookahead=3):
    """Find a label in line list and return the next useful line(s)."""
    label_norm = re.sub(r"[:\s]+$", "", label.strip().lower())

    stop_labels = {
        "property address", "rps order id", "emv", "client name",
        "add-ons requested", "appraisal type", "condition date",
        "contact name", "contact number", "special instruction",
        "special instructions", "for additional instructions, please review",
        "regards,", "rps | real property solutions"
    }

    for i, line in enumerate(lines):
        line_norm = re.sub(r"[:\s]+$", "", line.strip().lower())
        if line_norm == label_norm:
            collected = []
            for j in range(i + 1, min(i + 1 + max_lookahead, len(lines))):
                candidate = lines[j].strip()
                if not candidate:
                    continue
                candidate_lower = candidate.lower()
                if (
                    candidate_lower in stop_labels
                    or candidate_lower.startswith("for additional instructions")
                    or candidate_lower.startswith("if you have any questions")
                    or candidate_lower.startswith("appraisals | data | insights")
                    or candidate_lower.startswith("a brookfield company")
                ):
                    break
                collected.append(candidate)
                if label_norm not in {"special instruction", "special instructions"}:
                    break
            return " ".join(collected).strip()

    return ""


# ─────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────

def clean_special_instructions(raw: str) -> str:
    """Clean special instructions while preserving the meaningful instruction."""
    if not raw:
        return ""

    text = raw.strip()

    # Remove boilerplate/footer if it leaked in
    text = re.split(
        r'For additional instructions|Regards,|If you have any questions|Appraisals\s*\|\s*Data\s*\|\s*Insights|A Brookfield Company',
        text,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0].strip()

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip(' ,;')

    return text


def detect_lender(text: str) -> str:
    """Detect major Canadian lenders from raw special instructions."""
    if not text:
        return ""
    m = re.search(r'(RBC|BMO|TD|Scotiabank|National Bank|CIBC)', text, re.IGNORECASE)
    return m.group(1).upper() if m else ""


def _standardize(result: dict, vendor: str, status: str = "new") -> dict:
    """Guarantee consistent output shape + backward compatibility."""
    mortgage_val = result.get("mortgage", "") or result.get("form_type", "")
    return {
        "vendor": vendor,
        "status": status,
        "address": result.get("address", "").strip(),
        "order_id": result.get("order_id", ""),
        "client_name": result.get("client_name", ""),
        "lender": result.get("lender", ""),
        "mortgage": mortgage_val,
        "mortgage_type": mortgage_val,
        "who_pays": result.get("who_pays", ""),
        "due_date": result.get("who_pays", "") or result.get("cof_deadline", ""),
        "cof_deadline": result.get("cof_deadline", ""),
        "special_instructions": result.get("special_instructions", ""),
        "emv": result.get("emv", ""),
        "contact_name": result.get("contact_name", ""),
        "contact_number": result.get("contact_number", ""),
    }


# ─────────────────────────────────────────────
# PARSERS
# ─────────────────────────────────────────────

def extract_rps_from_html(html, subject=""):
    lines = html_to_lines(html)
    special_raw = get_line_value(lines, "Special Instruction", max_lookahead=10)

    raw = {
        "address": get_line_value(lines, "Property Address"),
        "order_id": get_line_value(lines, "RPS Order ID"),
        "client_name": get_line_value(lines, "Client Name"),
        "mortgage": get_line_value(lines, "Appraisal Type"),
        "who_pays": get_line_value(lines, "Condition Date"),
        "lender": detect_lender(special_raw),
        "special_instructions": clean_special_instructions(special_raw),
        "cof_deadline": "",
        "emv": get_line_value(lines, "EMV"),
        "contact_name": get_line_value(lines, "Contact Name"),
        "contact_number": get_line_value(lines, "Contact Number"),
    }

    if not raw["address"]:
        m = re.search(r"[–—-]\s*(.+?)\s*$", subject)
        if m:
            raw["address"] = m.group(1).strip()

    raw["address"] = re.sub(r"^[A-Z]\s+", "", raw["address"]).strip()
    raw["order_id"] = re.sub(r"[^\d]", "", raw["order_id"])
    raw["emv"] = re.sub(r"[^\d.,]", "", raw["emv"])
    raw["contact_number"] = re.sub(r"[^\d]", "", raw["contact_number"])

    return _standardize(raw, "RPS", "new")


def extract_rps_subject(subject, body):
    if "<html" in body.lower() or "<table" in body.lower() or "<div" in body.lower():
        return extract_rps_from_html(body, subject)

    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines() if line.strip()]
    special_raw = get_line_value(lines, "Special Instruction", max_lookahead=10)

    raw = {
        "address": get_line_value(lines, "Property Address"),
        "order_id": get_line_value(lines, "RPS Order ID"),
        "client_name": get_line_value(lines, "Client Name"),
        "mortgage": get_line_value(lines, "Appraisal Type"),
        "who_pays": get_line_value(lines, "Condition Date"),
        "lender": detect_lender(special_raw),
        "special_instructions": clean_special_instructions(special_raw),
        "cof_deadline": "",
        "emv": get_line_value(lines, "EMV"),
        "contact_name": get_line_value(lines, "Contact Name"),
        "contact_number": get_line_value(lines, "Contact Number"),
    }

    if not raw["address"]:
        m = re.search(r"[–—-]\s*(.+?)\s*$", subject)
        if m:
            raw["address"] = m.group(1).strip()

    raw["address"] = re.sub(r"^[A-Z]\s+", "", raw["address"]).strip()
    raw["order_id"] = re.sub(r"[^\d]", "", raw["order_id"])
    raw["emv"] = re.sub(r"[^\d.,]", "", raw["emv"])
    raw["contact_number"] = re.sub(r"[^\d]", "", raw["contact_number"])

    return _standardize(raw, "RPS", "new")


def extract_rps_cancelled_subject(subject, body):
    match = re.search(r'Order ID\s+(\d+)', subject)
    order_id = match.group(1) if match else ""
    parts = subject.split(" - ")
    lender = parts[2].strip() if len(parts) >= 3 else ""
    address = parts[3].strip() if len(parts) >= 4 else ""
    address = re.sub(r'\s*-\s*FULL_APPRAISAL.*$', '', address).strip()
    raw = {"address": address, "order_id": order_id, "lender": lender}
    return _standardize(raw, "RPS", "cancelled")


def extract_solidifi_body(subject, body):
    lines = html_to_lines(body)
    match = re.search(r'(OR\d+)', subject)
    order_id = match.group(1) if match else get_line_value(lines, "Order ID Number")

    raw = {
        "address": get_line_value(lines, "Property Address"),
        "order_id": order_id,
        "lender": get_line_value(lines, "Lender"),
        "client_name": get_line_value(lines, "Borrower Name"),
        "mortgage": get_line_value(lines, "Appraisal Form Type"),
        "who_pays": get_line_value(lines, "Due Date"),
        "special_instructions": clean_special_instructions(
            get_line_value(lines, "Special Instructions", max_lookahead=8)
        ),
    }

    if not raw["address"]:
        parts = subject.split(" - ")
        if len(parts) >= 3:
            raw["address"] = re.sub(r'\s*-?\s*OR\d+.*$', '', parts[2]).strip()

    return _standardize(raw, "Solidifi", "new")


def extract_nationwide_body(subject, body):
    """COF Deadline used as due date; falls back to Loan Type if absent."""
    order_id = ""
    match = re.search(r'NAS\s*#?\s*(\d+)', subject, re.IGNORECASE)
    if match:
        order_id = "NAS#" + match.group(1)

    raw = {
        "address": "",
        "order_id": order_id,
        "lender": "",
        "client_name": "",
        "mortgage": "",
        "who_pays": "",
        "cof_deadline": "",
        "special_instructions": "",
        "loan_type": "",
    }

    if body:
        m = re.search(r'Property Address[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if m: raw["address"] = m.group(1).strip()
        m = re.search(r'Lender[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if m: raw["lender"] = m.group(1).strip()
        m = re.search(r'Applicant Name[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if m: raw["client_name"] = m.group(1).strip()
        m = re.search(r'Service Type[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if m: raw["mortgage"] = m.group(1).strip()
        m = re.search(r'Loan Type[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if m: raw["loan_type"] = m.group(1).strip()
        m = re.search(r'COF Deadline[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if m: raw["cof_deadline"] = m.group(1).strip()
        m = re.search(
            r'IMPORTANT NOTES.*?FROM CLIENT[:\s]*\n(.+?)(?:\n\n|\{By clicking|$)',
            body, re.IGNORECASE | re.DOTALL
        )
        if m:
            raw["special_instructions"] = clean_special_instructions(m.group(1).strip())

    raw["who_pays"] = raw["cof_deadline"] if raw["cof_deadline"] else raw["loan_type"]
    return _standardize(raw, "Nationwide", "new")


def extract_nas_special_subject(subject, body):
    match = re.search(r'NAS\s*#?\s*(\d+)', subject, re.IGNORECASE)
    order_id = "NAS#" + match.group(1) if match else ""
    match = re.search(r'\|\s*(?:Address\s*:\s*)?(.+?)$', subject, re.IGNORECASE)
    address = match.group(1).strip() if match else ""
    raw = {"address": address, "order_id": order_id}
    return _standardize(raw, "Nationwide", "new")


def extract_alpine_body(subject, body):
    match = re.search(r'#(\d+)', subject)
    order_id = "#" + match.group(1) if match else ""

    match = re.search(r'#\d+\s+(.+?)\s*-\s*Appraisal', subject)
    client_name = match.group(1).strip() if match else ""

    address = ""
    m = re.search(
        r'Home Email:[^\n]+\n(.+?)\n(.+?(?:ON|BC|AB|QC|MB|SK|NS|NB|PE|NL)\s+\w+\d\w+)',
        body, re.IGNORECASE | re.DOTALL
    )
    if m:
        address = m.group(1).strip() + ", " + m.group(2).strip()

    mortgage = ""
    if re.search(r'1st mortgage|first mortgage', body, re.IGNORECASE):
        mortgage = "1st Mortgage"
    elif re.search(r'2nd mortgage|second mortgage', body, re.IGNORECASE):
        mortgage = "2nd Mortgage"

    who_pays = ""
    if re.search(r'Our Company will pay', body, re.IGNORECASE):
        who_pays = "Co. Pays"
    elif re.search(r'The Client will pay', body, re.IGNORECASE):
        who_pays = "Client Pays"

    lender = ""
    m = re.search(r'intended user to be[:\s*]+(.+?)(?:\*|\n|$)', body, re.IGNORECASE)
    if m:
        lender = m.group(1).strip().strip('*').strip()

    raw = {
        "address": address,
        "order_id": order_id,
        "client_name": client_name,
        "mortgage": mortgage,
        "lender": lender,
        "who_pays": who_pays,
    }
    return _standardize(raw, "Alpine", "new")


def extract_alpine_cancelled(subject, body):
    match = re.search(r'#(\d+)', subject)
    order_id = "#" + match.group(1) if match else ""
    raw = {"order_id": order_id}
    return _standardize(raw, "Alpine", "cancelled")


# ─────────────────────────────────────────────
# EXTRACTORS DICTIONARY
# ─────────────────────────────────────────────

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
    """Send alert to Home Assistant as a sensor state + event + history entry."""
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }

    # Build display text
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
        lines.append(f"Due: {alert['who_pays']}")
    if alert.get("emv"):
        lines.append(f"EMV: ${alert['emv']}")
    if alert.get("contact_name") or alert.get("contact_number"):
        lines.append(f"Contact: {alert.get('contact_name', '')} {alert.get('contact_number', '')}".strip())
    if alert.get("lender"):
        lines.append(f"Lender: {alert['lender']}")
    if alert.get("special_instructions"):
        lines.append(f"Instructions: {alert['special_instructions']}")

    display_text = "\n".join(lines)

    # 1. Load and update history FIRST
    history_file = "/data/appraisal_history.json"
    try:
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
            "who_pays": alert.get("who_pays", ""),
            "due_date": alert.get("who_pays", ""),
            "lender": alert.get("lender", ""),
            "client_name": alert.get("client_name", ""),
            "alert_type": alert.get("alert_type", ""),
            "special_instructions": alert.get("special_instructions", ""),
            "vendor": alert.get("vendor", ""),
            "status": alert.get("status", ""),
        })
        history = history[:20]

        with open(history_file, "w") as f:
            json.dump(history, f)
        log.info("✅ History updated")
    except Exception as e:
        log.error(f"❌ History update failed: {e}")
        history = []

    # 2. Update HA sensor
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
            "special_instructions": alert.get("special_instructions", ""),
            "cof_deadline": alert.get("cof_deadline", ""),
            "buzzer": alert.get("buzzer", ""),
            "display_text": display_text,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "rule_name": alert.get("rule_name", ""),
            "emv": alert.get("emv", ""),
            "contact_name": alert.get("contact_name", ""),
            "contact_number": alert.get("contact_number", ""),
            "vendor": alert.get("vendor", ""),
            "status": alert.get("status", ""),
            "history": history,
        }
    }

    log.info(f"DEBUG send_to_ha len(history)={len(history)}")
    log.info(f"DEBUG send_to_ha first history item={history[0] if history else None}")
    log.info(
        f"DEBUG send_to_ha sensor_payload attribute keys={list(sensor_payload['attributes'].keys())}"
    )
    log.info(
        f"DEBUG send_to_ha has history attribute={'history' in sensor_payload['attributes']}"
    )

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

    # 3. Fire HA event
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
        log.info("✅ HA event fired: appraisal_alert")
    except Exception as e:
        log.error(f"❌ Failed to fire HA event: {e}")

# ─────────────────────────────────────────────
# EMAIL MATCHING
# ─────────────────────────────────────────────

def match_rule(rule, from_addr, subject, body, account_email):
    """Check if an email matches a rule."""
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
    """Process a single email against all rules."""
    from_raw = msg.get("From", "")
    subject = decode_header_value(msg.get("Subject", ""))
    body = get_email_body(msg)

    match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', from_raw)
    from_addr = match.group(0).lower() if match else from_raw.lower()

    log.info(f"📧 Processing: FROM={from_addr} | SUBJECT={subject[:60]}")

    for rule in RULES:
        if match_rule(rule, from_addr, subject, body, account_email):
            log.info(f"✅ Matched rule: {rule['name']}")

            extractor = EXTRACTORS.get(rule["extract"])
            extracted = extractor(subject, body) if extractor else {}

            # Debug mode: log parsed lines and extracted fields
            if DEBUG_MODE:
                if "<html" in body.lower() or "<table" in body.lower():
                    parsed_lines = html_to_lines(body)
                else:
                    parsed_lines = [l.strip() for l in body.splitlines() if l.strip()]
                log.info(f"🔍 DEBUG — First 30 parsed lines:")
                for i, line in enumerate(parsed_lines[:30]):
                    log.info(f"  [{i:02d}] {line}")
                log.info(f"🔍 DEBUG — Extracted fields:")
                for k, v in extracted.items():
                    if v:
                        log.info(f"  {k}: {v}")

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
    if DEBUG_MODE:
        if "<html" in body.lower() or "<table" in body.lower():
            parsed_lines = html_to_lines(body)
        else:
            parsed_lines = [l.strip() for l in body.splitlines() if l.strip()]
        log.info(f"🔍 DEBUG — Unmatched email, first 30 parsed lines:")
        for i, line in enumerate(parsed_lines[:30]):
            log.info(f"  [{i:02d}] {line}")
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
            process_email(msg, email_addr)
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
