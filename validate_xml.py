"""
Validator for SMS Backup & Restore XML files.
Checks against the field spec at:
https://www.synctech.com.au/sms-backup-restore/fields-in-xml-backup-files/
"""

import sys
import re
from xml.etree import ElementTree as ET
from collections import defaultdict

ISSUES = defaultdict(list)
warnings = []
errors = []

VALID_SMS_TYPES = {"1", "2", "3", "4", "5", "6"}
VALID_MMS_MSG_BOX = {"1", "2", "3", "4"}
VALID_ADDR_TYPES = {"129", "130", "151", "137"}
VALID_MMS_M_TYPES = {
    "128",  # m-send-req (sent)
    "132",  # m-retrieve-conf (received)
    "130",  # m-send-conf
    "134",  # m-notification-ind
}

def check_required(element, tag, attrs, index):
    missing = [a for a in attrs if element.get(a) is None]
    for a in missing:
        errors.append(f"<{tag}> #{index}: missing required attribute '{a}'")

def check_nonempty(element, tag, attrs, index):
    for a in attrs:
        val = element.get(a)
        if val is not None and val.strip() == "":
            warnings.append(f"<{tag}> #{index}: attribute '{a}' is empty string")

def check_integer(element, tag, attr, index):
    val = element.get(attr)
    if val is None:
        return
    try:
        int(val)
    except ValueError:
        errors.append(f"<{tag}> #{index}: attribute '{attr}' is not an integer: {val!r}")

def check_unix_ms(element, tag, attr, index):
    val = element.get(attr)
    if val is None:
        return
    try:
        ts = int(val)
        # Sanity: should be between year 2000 and 2040
        if not (946684800000 <= ts <= 2208988800000):
            warnings.append(f"<{tag}> #{index}: '{attr}' timestamp looks out of range: {val}")
    except ValueError:
        errors.append(f"<{tag}> #{index}: '{attr}' is not a valid timestamp: {val!r}")

def check_phone(element, tag, attr, index):
    val = element.get(attr)
    if val is None or val == "null":
        return
    # Group MMS uses ~ to separate multiple numbers — that's valid
    numbers = val.split("~")
    for num in numbers:
        num = num.strip()
        if not re.match(r'^\+?[\d\-\(\)\s]+$', num) and num != "~unknown~":
            warnings.append(f"<{tag}> #{index}: '{attr}' contains unusual number: {num!r}")

def validate(xml_file):
    print(f"Parsing {xml_file} ...")
    try:
        tree = ET.parse(xml_file)
    except ET.ParseError as e:
        print(f"FATAL: XML parse error: {e}")
        sys.exit(1)

    root = tree.getroot()
    if root.tag != "smses":
        errors.append(f"Root element is <{root.tag}>, expected <smses>")

    # Check count attribute
    declared_count = root.get("count")
    sms_elements = root.findall("sms")
    mms_elements = root.findall("mms")
    actual_count = len(sms_elements) + len(mms_elements)
    if declared_count is not None:
        if int(declared_count) != actual_count:
            warnings.append(
                f"<smses count> says {declared_count} but found {actual_count} "
                f"(sms={len(sms_elements)}, mms={len(mms_elements)})"
            )
    print(f"  Found {len(sms_elements)} <sms> and {len(mms_elements)} <mms> elements")

    # --- Validate <sms> elements ---
    for i, sms in enumerate(sms_elements, 1):
        check_required(sms, "sms", ["protocol", "address", "date", "type", "body"], i)
        check_integer(sms, "sms", "protocol", i)
        check_unix_ms(sms, "sms", "date", i)
        check_phone(sms, "sms", "address", i)

        sms_type = sms.get("type")
        if sms_type and sms_type not in VALID_SMS_TYPES:
            errors.append(f"<sms> #{i}: invalid type={sms_type!r} (expected 1-6)")

        read = sms.get("read")
        if read and read not in {"0", "1"}:
            warnings.append(f"<sms> #{i}: unusual read value: {read!r}")

    # --- Validate <mms> elements ---
    for i, mms in enumerate(mms_elements, 1):
        check_required(mms, "mms", ["address", "date", "msg_box", "ct_t"], i)
        check_unix_ms(mms, "mms", "date", i)
        check_phone(mms, "mms", "address", i)

        msg_box = mms.get("msg_box")
        if msg_box and msg_box not in VALID_MMS_MSG_BOX:
            errors.append(f"<mms> #{i}: invalid msg_box={msg_box!r} (expected 1-4)")

        m_type = mms.get("m_type")
        if m_type and m_type not in VALID_MMS_M_TYPES:
            warnings.append(f"<mms> #{i}: unusual m_type={m_type!r}")

        ct_t = mms.get("ct_t")
        if ct_t and ct_t != "application/vnd.wap.multipart.related":
            warnings.append(f"<mms> #{i}: unusual ct_t={ct_t!r}")

        # Validate <parts>
        parts_el = mms.find("parts")
        if parts_el is None:
            errors.append(f"<mms> #{i}: missing <parts> element")
        else:
            parts = parts_el.findall("part")
            if len(parts) == 0:
                warnings.append(f"<mms> #{i}: <parts> has no <part> children")
            for j, part in enumerate(parts, 1):
                check_required(part, f"mms[{i}]/part", ["seq", "ct"], j)
                seq = part.get("seq")
                if seq is not None:
                    try:
                        int(seq)
                    except ValueError:
                        errors.append(f"<mms> #{i} <part> #{j}: seq is not integer: {seq!r}")
                ct = part.get("ct")
                if ct:
                    # data should be present for non-text parts
                    if not ct.startswith("text/") and part.get("data") in (None, "null", ""):
                        warnings.append(
                            f"<mms> #{i} <part> #{j}: ct={ct!r} but data is missing/null"
                        )

        # Validate <addrs>
        addrs_el = mms.find("addrs")
        if addrs_el is not None:
            for j, addr in enumerate(addrs_el.findall("addr"), 1):
                check_required(addr, f"mms[{i}]/addr", ["address", "type", "charset"], j)
                addr_type = addr.get("type")
                if addr_type and addr_type not in VALID_ADDR_TYPES:
                    warnings.append(
                        f"<mms> #{i} <addr> #{j}: unusual type={addr_type!r} "
                        f"(expected 129=BCC, 130=CC, 151=To, 137=From)"
                    )

    # --- Check for encoding issues (mojibake from latin1/utf8 mix-up) ---
    print("  Checking for encoding issues...")
    with open(xml_file, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    mojibake_patterns = [
        (r'â€™', "right single quote encoded as latin1 (â€™ → ')"),
        (r'â€œ', "left double quote encoded as latin1"),
        (r'â€\x9d', "right double quote encoded as latin1"),
        (r'ðŸ',    "emoji encoded as latin1 (mojibake)"),
        (r'â˜',    "symbol encoded as latin1 (mojibake)"),
        (r'Ã©',    "accented char encoded as latin1 (mojibake)"),
    ]
    for pattern, description in mojibake_patterns:
        count = len(re.findall(pattern, content))
        if count > 0:
            warnings.append(f"Encoding issue: {count} instance(s) of {description}")

    # --- Summary ---
    print()
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
    else:
        print("  No errors found.")

    print()
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")
    else:
        print("  No warnings.")

    print()
    print(f"Summary: {len(errors)} error(s), {len(warnings)} warning(s)")
    print(f"         {actual_count} total messages ({len(sms_elements)} SMS, {len(mms_elements)} MMS)")


if __name__ == "__main__":
    xml_file = sys.argv[1] if len(sys.argv) > 1 else "gvoice-all.xml"
    validate(xml_file)
