import imaplib
import email
import json
import csv
import re
from datetime import datetime
from email.header import decode_header
from pathlib import Path

from config import YAHOO_EMAIL, YAHOO_APP_PASSWORD, IMAP_SERVER, IMAP_PORT


BASE_DIR = Path(__file__).resolve().parent.parent
RULES_FILE = BASE_DIR / "rules.json"


def decode_text(value: str | None) -> str:
    if not value:
        return ""

    result = ""

    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            try:
                safe_encoding = encoding or "utf-8"

                if safe_encoding.lower() == "unknown-8bit":
                    safe_encoding = "utf-8"

                result += part.decode(safe_encoding, errors="replace")

            except (LookupError, UnicodeDecodeError):
                result += part.decode("utf-8", errors="replace")

        else:
            result += part

    return result.strip()


def load_rules() -> dict:
    with open(RULES_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def contains_any(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()

    for keyword in keywords:
        keyword_clean = keyword.strip()

        if not keyword_clean:
            continue

        pattern = r"\b" + re.escape(keyword_clean.lower()) + r"\b"

        if re.search(pattern, text_lower):
            return True

    return False

def extract_email_body(message) -> str:
    body = ""

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if "attachment" in content_disposition:
                continue

            if content_type == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"

                    body += payload.decode(charset, errors="replace")
                except Exception:
                    pass
    else:
        try:
            payload = message.get_payload(decode=True)
            charset = message.get_content_charset() or "utf-8"

            body = payload.decode(charset, errors="replace")
        except Exception:
            pass

    return body

def contains_any_with_match(text: str, keywords: list[str]) -> tuple[bool, str]:
    text_lower = text.lower()

    for keyword in keywords:
        keyword_clean = keyword.strip()

        if not keyword_clean:
            continue

        pattern = r"\b" + re.escape(keyword_clean.lower()) + r"\b"

        if re.search(pattern, text_lower):
            return True, keyword

    return False, ""


def find_matching_rule(subject: str, sender: str, body: str, rules: list[dict]) -> tuple[dict | None, str]:
    enabled_rules = [rule for rule in rules if rule.get("enabled", True)]
    enabled_rules.sort(key=lambda r: r.get("priority", 9999))

    searchable_text = f"{subject}\n{sender}\n{body}"

    for rule in enabled_rules:
        match_type = rule.get("matchType", "any").lower()

        required_keywords = rule.get("requiredContains", [])
        subject_keywords = rule.get("subjectContains", [])
        from_keywords = rule.get("fromContains", [])
        body_keywords = rule.get("bodyContains", [])

        if any(k.strip() for k in required_keywords):
            required_ok = all(k.strip().lower() in searchable_text.lower() for k in required_keywords if k.strip())
            if not required_ok:
                continue

        checks = []

        subject_match, subject_keyword = contains_any_with_match(subject, subject_keywords)
        from_match, from_keyword = contains_any_with_match(sender, from_keywords)
        body_match, body_keyword = contains_any_with_match(body, body_keywords)

        if any(k.strip() for k in subject_keywords):
            checks.append(("subject", subject_match, subject_keyword))

        if any(k.strip() for k in from_keywords):
            checks.append(("from", from_match, from_keyword))

        if any(k.strip() for k in body_keywords):
            checks.append(("body", body_match, body_keyword))

        if not checks:
            continue

        if match_type == "any":
            for field, matched, keyword in checks:
                if matched:
                    return rule, f"{field} contains '{keyword}'"

        if match_type == "all" and all(matched for _, matched, _ in checks):
            reasons = [f"{field} contains '{keyword}'" for field, _, keyword in checks if keyword]
            return rule, " AND ".join(reasons)

    return None, ""

def build_search_criteria(read_filter: str) -> str:
    match read_filter.lower():
        case "all":
            return "ALL"
        case "read":
            return "SEEN"
        case "unread":
            return "UNSEEN"
        case _:
            raise ValueError("readFilter must be one of: all, read, unread")

def contains_all(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return all(keyword.strip().lower() in text_lower for keyword in keywords if keyword.strip())

def is_message_read(mail, msg_id: bytes) -> bool:
    status, flags_data = mail.fetch(msg_id, "(FLAGS)")
    if status != "OK":
        return False

    flags_text = " ".join(
        part.decode(errors="ignore") if isinstance(part, bytes) else str(part)
        for part in flags_data
        if part
    )

    return "\\Seen" in flags_text or "\\SEEN" in flags_text

def write_log_row(row: dict) -> Path:
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)

    log_file = logs_dir / f"mail_matches_{datetime.now().strftime('%Y-%m-%d')}.csv"

    file_exists = log_file.exists()

    with open(log_file, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "dry_run",
                "email_id",
                "read",
                "matched",
                "rule",
                "target_folder",
                "action",
                "from",
                "subject",
                "match_reason"
            ]
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)

    return log_file

def main() -> None:
    config = load_rules()
    dry_run = config.get("dryRun", True)
    limit = config.get("limit", 20)
    rules = config.get("rules", [])
    last_log_file = None

    print("Connecting to Yahoo IMAP...")
    print(f"Account: {YAHOO_EMAIL}")
    print(f"Dry run: {dry_run}")
    print(f"Limit: {limit}")

    with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT) as mail:
        mail.login(YAHOO_EMAIL, YAHOO_APP_PASSWORD)
        mail.select("INBOX")

        read_filter = config.get("readFilter", "read")

        if read_filter == "all":
            print("\n⚠ WARNING ⚠")
            print("You are running against ALL emails.")
            print("This may include unread or important emails.")
            print("Press Ctrl+C within 5 seconds to cancel...\n")

            import time
            time.sleep(5)

        search_criteria = build_search_criteria(read_filter)

        print(f"Read filter: {read_filter}")

        status, data = mail.search(None, search_criteria)
        if status != "OK":
            raise RuntimeError("Failed to search INBOX")

        message_ids = data[0].split()
        latest_ids = message_ids[-limit:]
        total_emails = len(latest_ids)
        processed_count = 0
        matched_count = 0

        print(f"\nChecking latest {len(latest_ids)} emails...")
        print("-" * 70)

        for msg_id in latest_ids:
            processed_count += 1

            print(
                f"\n[{processed_count}/{total_emails}] "
                f"Processing email ID {msg_id.decode()}..."
            )

            # Get real read/unread status before reading the body.
            is_read = is_message_read(mail, msg_id)

            # BODY.PEEK[] fetches the email without marking it as read.
            status, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK":
                print(f"Failed to fetch message {msg_id.decode()}")
                continue

            raw_response = msg_data[0]
            message = email.message_from_bytes(raw_response[1])

            subject = decode_text(message.get("Subject"))
            sender = decode_text(message.get("From"))
            body = extract_email_body(message)

            rule, match_reason = find_matching_rule(subject, sender, body, rules)

            if rule:
                matched_count += 1
                print("MATCH")
                print(f"Read: {'YES' if is_read else 'NO'}")
                print(f"ID: {msg_id.decode()}")
                print(f"Rule: {rule['name']}")
                print(f"Target folder: {rule['folder']}")
                print(f"From: {sender}")
                print(f"Subject: {subject}")
                print(f"Match reason: {match_reason}")
                print("-" * 70)
                last_log_file = write_log_row({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "dry_run": dry_run,
                    "email_id": msg_id.decode(),
                    "read": "YES" if is_read else "NO",
                    "matched": "YES",
                    "rule": rule["name"],
                    "target_folder": rule["folder"],
                    "action": rule.get("action", "move"),
                    "from": sender,
                    "subject": subject,
                    "match_reason": match_reason
                })
            else:
                if config.get("logUnmatched", False):
                    write_log_row({
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "dry_run": dry_run,
                        "email_id": msg_id.decode(),
                        "read": "YES" if is_read else "NO",
                        "matched": "NO",
                        "rule": "UNMATCHED",
                        "target_folder": "",
                        "action": "none",
                        "from": sender,
                        "subject": subject
                    })

        if last_log_file:
            print("\nLog file created:")
            print(last_log_file)
        else:
            print("\nNo matching emails found. No log file created.")

        print("\nRun summary")
        print("-" * 40)
        print(f"Processed emails : {processed_count}")
        print(f"Matched emails   : {matched_count}")
        print(f"Unmatched emails : {processed_count - matched_count}")

        mail.logout()


if __name__ == "__main__":
    main()