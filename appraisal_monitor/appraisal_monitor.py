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
    return clean.strip()

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
    """
    Find a label in line list and return the next useful line(s).
    """
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
                if candidate.lower() in stop_labels:
                    break
                collected.append(candidate)
                if label_norm not in {"special instruction", "special instructions"}:
                    break
            return " ".join(collected).strip()

    return ""


def extract_rps_from_html(html, subject=""):
    """Final RPS parser - cleans Special Instructions and detects lender reliably."""
    lines = html_to_lines(html)

    address = get_line_value(lines, "Property Address")
    order_id = get_line_value(lines, "RPS Order ID")
    emv = get_line_value(lines, "EMV")
    client_name = get_line_value(lines, "Client Name")
    appraisal_type = get_line_value(lines, "Appraisal Type")
    condition_date = get_line_value(lines, "Condition Date")
    contact_name = get_line_value(lines, "Contact Name")
    contact_number = get_line_value(lines, "Contact Number")

    # Get raw special instructions for lender detection
    special_raw = get_line_value(lines, "Special Instruction", max_lookahead=10)

    # Clean special instructions
    special_instructions = special_raw
    if special_instructions:
        if "For additional instructions" in special_instructions:
            special_instructions = special_instructions.split("For additional instructions", 1)[0].strip()

        special_instructions = re.sub(r'^\s*\(Purchase\)|\(Refinance\)|\(Sale\)', '', special_instructions, flags=re.IGNORECASE)
        special_instructions = re.sub(r',?contact:.*?(?=\s|$|;)', '', special_instructions, flags=re.IGNORECASE)
        special_instructions = re.sub(r',?EMV:.*?(?=\s|$|;)', '', special_instructions)
        special_instructions = re.sub(r'\b[\w\.-]+@[\w\.-]+\.\w+\b', '', special_instructions)
        special_instructions = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '', special_instructions)
        special_instructions = re.sub(r'Requester Name:.*?;', '', special_instructions, flags=re.IGNORECASE)
        special_instructions = re.sub(r';PropertyStyle:.*?(?=,|$)', '', special_instructions)
        special_instructions = re.sub(r'\s+', ' ', special_instructions).strip(' ,;')

    # Lender detection
    lender = ""
    if special_raw:
        m = re.search(r'(RBC|BMO|TD|Scotiabank|National Bank|CIBC)', special_raw, re.IGNORECASE)
        if m:
            lender = m.group(1).upper()

    if not address:
        m = re.search(r"[–—-]\s*(.+?)\s*$", subject)
        if m:
            address = m.group(1).strip()

    address = re.sub(r"^[A-Z]\s+", "", address).strip()
    order_id = re.sub(r"[^\d]", "", order_id)
    emv = re.sub(r"[^\d.,]", "", emv)
    contact_number = re.sub(r"[^\d]", "", contact_number)

    return {
        "address": address,
        "order_id": order_id,
        "client_name": client_name,
        "mortgage": appraisal_type,
        "who_pays": condition_date,
        "lender": lender,
        "special_instructions": special_instructions,
        "cof_deadline": "",
        "emv": emv,
        "contact_name": contact_name,
        "contact_number": contact_number,
    }


def extract_rps_subject(subject, body):
    """RPS extractor - prefers HTML parsing."""
    if "<html" in body.lower() or "<table" in body.lower() or "<div" in body.lower():
        return extract_rps_from_html(body, subject)

    # Plain-text fallback
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines() if line.strip()]

    address = get_line_value(lines, "Property Address")
    order_id = get_line_value(lines, "RPS Order ID")
    emv = get_line_value(lines, "EMV")
    client_name = get_line_value(lines, "Client Name")
    appraisal_type = get_line_value(lines, "Appraisal Type")
    condition_date = get_line_value(lines, "Condition Date")
    contact_name = get_line_value(lines, "Contact Name")
    contact_number = get_line_value(lines, "Contact Number")
    special_raw = get_line_value(lines, "Special Instruction", max_lookahead=10)

    special_instructions = special_raw
    if special_instructions:
        if "For additional instructions" in special_instructions:
            special_instructions = special_instructions.split("For additional instructions", 1)[0].strip()
        special_instructions = re.sub(r'^\s*\(Purchase\)|\(Refinance\)|\(Sale\)', '', special_instructions, flags=re.IGNORECASE)
        special_instructions = re.sub(r',?contact:.*?(?=\s|$|;)', '', special_instructions, flags=re.IGNORECASE)
        special_instructions = re.sub(r',?EMV:.*?(?=\s|$|;)', '', special_instructions)
        special_instructions = re.sub(r'\b[\w\.-]+@[\w\.-]+\.\w+\b', '', special_instructions)
        special_instructions = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '', special_instructions)
        special_instructions = re.sub(r'Requester Name:.*?;', '', special_instructions, flags=re.IGNORECASE)
        special_instructions = re.sub(r';PropertyStyle:.*?(?=,|$)', '', special_instructions)
        special_instructions = re.sub(r'\s+', ' ', special_instructions).strip(' ,;')

    lender = ""
    if special_raw:
        m = re.search(r'(RBC|BMO|TD|Scotiabank|National Bank|CIBC)', special_raw, re.IGNORECASE)
        if m:
            lender = m.group(1).upper()

    if not address:
        m = re.search(r"[–—-]\s*(.+?)\s*$", subject)
        if m:
            address = m.group(1).strip()

    address = re.sub(r"^[A-Z]\s+", "", address).strip()
    order_id = re.sub(r"[^\d]", "", order_id)
    emv = re.sub(r"[^\d.,]", "", emv)
    contact_number = re.sub(r"[^\d]", "", contact_number)

    return {
        "address": address,
        "order_id": order_id,
        "client_name": client_name,
        "mortgage": appraisal_type,
        "who_pays": condition_date,
        "lender": lender,
        "special_instructions": special_instructions,
        "cof_deadline": "",
        "emv": emv,
        "contact_name": contact_name,
        "contact_number": contact_number,
    }


def extract_rps_cancelled_subject(subject, body):
    """Extract from RPS cancelled subject."""
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
        address = re.sub(r'\s*-\s*FULL_APPRAISAL.*$', '', address).strip()
    return {"address": address, "order_id": order_id, "lender": lender, "mortgage": "", "who_pays": ""}


def extract_solidifi_body(subject, body):
    """Improved Solidifi parser using clean line extraction."""
    lines = html_to_lines(body)

    # Order ID
    match = re.search(r'(OR\d+)', subject)
    order_id = match.group(1) if match else get_line_value(lines, "Order ID Number")

    address = get_line_value(lines, "Property Address")
    lender = get_line_value(lines, "Lender")
    client_name = get_line_value(lines, "Borrower Name")
    form_type = get_line_value(lines, "Appraisal Form Type")
    due_date = get_line_value(lines, "Due Date")
    special_instructions = get_line_value(lines, "Special Instructions", max_lookahead=8)

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
    """Extract from Nationwide email body.
    COF Deadline now becomes the Due Date when present."""
    order_id = ""
    address = ""
    lender = ""
    client_name = ""
    service_type = ""
    loan_type = ""
    cof_deadline = ""
    special_instructions = ""

    match = re.search(r'NAS\s*#?\s*(\d+)', subject, re.IGNORECASE)
    if match:
        order_id = "NAS#" + match.group(1)

    if body:
        match = re.search(r'Property Address[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            address = match.group(1).strip()

        match = re.search(r'Lender[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            lender = match.group(1).strip()

        match = re.search(r'Applicant Name[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            client_name = match.group(1).strip()

        match = re.search(r'Service Type[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            service_type = match.group(1).strip()

        match = re.search(r'Loan Type[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            loan_type = match.group(1).strip()

        # COF Deadline - used as Due Date
        match = re.search(r'COF Deadline[:\s]+(.+?)(?:\n|$)', body, re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            if val:
                cof_deadline = val

        match = re.search(r'IMPORTANT NOTES.*?FROM CLIENT[:\s]*\n(.+?)(?:\n\n|\{By clicking|$)', body, re.IGNORECASE | re.DOTALL)
        if match:
            val = match.group(1).strip()
            if val:
                special_instructions = val

    # Use COF Deadline as Due Date if it exists, otherwise fall back to Loan Type
    due_date = cof_deadline if cof_deadline else loan_type

    return {
        "address": address,
        "order_id": order_id,
        "lender": lender,
        "client_name": client_name,
        "mortgage": service_type,
        "who_pays": due_date,           # ← This is what shows as "Due Date" in your card
        "special_instructions": special_instructions,
        "cof_deadline": cof_deadline
    }

def extract_nas_special_subject(subject, body):
    """Extract from Nationwide special subject."""
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
    """Extract from Alpine Credits cancellation reply."""
    order_id = ""
    match = re.search(r'#(\d+)', subject)
    if match:
        order_id = "#" + match.group(1)
    return {"address": "", "order_id": order_id, "lender": "", "mortgage": "", "who_pays": ""}
