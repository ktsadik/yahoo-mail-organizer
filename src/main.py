import imaplib
import email
import json
import csv
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
            result += part.decode(encoding or "utf-8", errors="replace")
        else:
            result += part

    return result


def load_rules() -> dict:
    with open(RULES_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def contains_any(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(keyword.strip().lower() in text_lower for keyword in keywords if keyword.strip())

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

def find_matching_rule(subject: str, sender: str, body: str, rules: list[dict]) -> dict | None:
    enabled_rules = [
        rule for rule in rules
        if rule.get("enabled", True)
    ]

    enabled_rules.sort(key=lambda r: r.get("priority", 9999))

    searchable_text = f"{subject}\n{sender}\n{body}"

    for rule in enabled_rules:
        match_type = rule.get("matchType", "any").lower()

        required_keywords = rule.get("requiredContains", [])
        subject_keywords = rule.get("subjectContains", [])
        from_keywords = rule.get("fromContains", [])
        body_keywords = rule.get("bodyContains", [])

        required_has_condition = any(keyword.strip() for keyword in required_keywords)

        if required_has_condition and not contains_all(searchable_text, required_keywords):
            continue

        subject_has_condition = any(keyword.strip() for keyword in subject_keywords)
        from_has_condition = any(keyword.strip() for keyword in from_keywords)
        body_has_condition = any(keyword.strip() for keyword in body_keywords)

        subject_matches = contains_any(subject, subject_keywords)
        from_matches = contains_any(sender, from_keywords)
        body_matches = contains_any(body, body_keywords)

        checks = []

        if subject_has_condition:
            checks.append(subject_matches)

        if from_has_condition:
            checks.append(from_matches)

        if body_has_condition:
            checks.append(body_matches)

        if not checks:
            continue

        if match_type == "any" and any(checks):
            return rule

        if match_type == "all" and all(checks):
            return rule

        if match_type not in ("any", "all"):
            raise ValueError(
                f"Invalid matchType '{match_type}' in rule '{rule.get('name', 'Unnamed')}'. "
                "Allowed values are: any, all"
            )

    return None

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
                "subject"
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

        print(f"\nChecking latest {len(latest_ids)} emails...")
        print("-" * 70)

        for msg_id in latest_ids:
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

            rule = find_matching_rule(subject, sender, body, rules)

            if rule:
                print("MATCH")
                print(f"Read: {'YES' if is_read else 'NO'}")
                print(f"ID: {msg_id.decode()}")
                print(f"Rule: {rule['name']}")
                print(f"Target folder: {rule['folder']}")
                print(f"From: {sender}")
                print(f"Subject: {subject}")
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
                    "subject": subject
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

        mail.logout()


if __name__ == "__main__":
    main()