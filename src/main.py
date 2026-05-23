import imaplib
import email
import json
import csv
import re
import time
from datetime import datetime
from email.header import decode_header
from pathlib import Path
from imapclient.imap_utf7 import encode as imap_utf7_encode

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


def find_matching_rule(subject: str, sender: str, body: str, rules: list[dict]) -> tuple[dict | None, str]:
    enabled_rules = [rule for rule in rules if rule.get("enabled", True)]
    enabled_rules.sort(key=lambda r: r.get("priority", 9999))

    searchable_text = f"{subject}\n{sender}\n{body}".lower()

    for rule in enabled_rules:
        match_type = rule.get("matchType", "any").lower()

        if match_type not in ("any", "all"):
            raise ValueError(
                f"Invalid matchType '{match_type}' in rule '{rule.get('name', 'Unnamed')}'. "
                "Allowed values are: any, all"
            )

        required_keywords = rule.get("requiredContains", [])
        subject_keywords = rule.get("subjectContains", [])
        from_keywords = rule.get("fromContains", [])
        body_keywords = rule.get("bodyContains", [])

        active_required = [k for k in required_keywords if k.strip()]
        if active_required:
            required_ok = all(k.strip().lower() in searchable_text for k in active_required)
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


def is_message_read(mail, msg_uid: bytes) -> bool:
    status, flags_data = mail.uid("fetch", msg_uid, "(FLAGS)")
    if status != "OK":
        return False

    flags_text = " ".join(
        part.decode(errors="ignore") if isinstance(part, bytes) else str(part)
        for part in flags_data
        if part
    )

    return "\\Seen" in flags_text or "\\SEEN" in flags_text

def get_selected_message_ids(mail, message_ids: list[bytes], read_filter: str, limit: int) -> list[bytes]:
    selected_ids = []

    for msg_id in reversed(message_ids):
        is_read = is_message_read(mail, msg_id)

        if read_filter == "read" and not is_read:
            continue

        if read_filter == "unread" and is_read:
            continue

        selected_ids.insert(0, msg_id)

        if len(selected_ids) >= limit:
            break

    return selected_ids


def move_email(mail, msg_id: bytes, target_folder: str) -> bool:
    folder_encoded = imap_utf7_encode(target_folder)
    folder_arg = b'"' + folder_encoded + b'"'

    print(f"Trying to move UID {msg_id.decode()} to folder: {target_folder}")
    print(f"Encoded folder: {folder_arg}")

    copy_status, copy_data = mail.uid("copy", msg_id, folder_arg)
    print(f"COPY status: {copy_status}, data: {copy_data}")

    if copy_status != "OK":
        return False

    store_status, store_data = mail.uid("store", msg_id, "+FLAGS", r"(\Deleted)")
    print(f"STORE status: {store_status}, data: {store_data}")

    if store_status != "OK":
        return False

    return True

def resolve_action(rule: dict, is_read: bool) -> tuple[str, str]:
    default_action = rule.get("action", "move")
    default_folder = rule.get("folder", "")

    action_by_read_status = rule.get("actionByReadStatus")

    if action_by_read_status:
        status_key = "read" if is_read else "unread"

        status_action = action_by_read_status.get(status_key)

        if status_action:
            return (
                status_action.get("action", default_action),
                status_action.get("folder", default_folder),
            )

    return default_action, default_folder

def write_log_row(row: dict) -> Path:
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)

    log_file = logs_dir / f"mail_matches_{datetime.now().strftime('%Y-%m-%d')}.csv"
    file_exists = log_file.exists()

    fieldnames = [
        "timestamp",
        "dry_run",
        "email_id",
        "read",
        "matched",
        "rule",
        "target_folder",
        "from",
        "subject",
        "action",
        "action_result",
        "match_reason",
    ]

    with open(log_file, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)

    return log_file

def main() -> None:
    config = load_rules()
    dry_run = config.get("dryRun", True)
    limit = config.get("limit", 20)
    read_filter = config.get("readFilter", "read").lower()
    log_unmatched = config.get("logUnmatched", False)
    rules = config.get("rules", [])

    last_log_file = None

    processed_count = 0
    matched_count = 0
    unmatched_count = 0
    move_success_count = 0
    move_failed_count = 0
    dry_run_count = 0

    print("Connecting to Yahoo IMAP...")
    print(f"Account: {YAHOO_EMAIL}")
    print(f"Dry run: {dry_run}")
    print(f"Limit: {limit}")
    print(f"Read filter: {read_filter}")

    if read_filter == "all":
        print("\n⚠ WARNING ⚠")
        print("You are running against ALL emails.")
        print("This may include unread or important emails.")
        print("Press Ctrl+C within 5 seconds to cancel...\n")
        time.sleep(5)

    with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT) as mail:
        mail.login(YAHOO_EMAIL, YAHOO_APP_PASSWORD)
        mail.select("INBOX")

        search_criteria = build_search_criteria(read_filter)

        status, data = mail.uid("search", None, search_criteria)
        if status != "OK":
            raise RuntimeError("Failed to search INBOX")

        message_ids = data[0].split()

        latest_ids = get_selected_message_ids(
            mail=mail,
            message_ids=message_ids,
            read_filter=read_filter,
            limit=limit,
        )

        print("\nSelected email IDs:")
        for x in latest_ids:
            print(x.decode())

        total_emails = len(latest_ids)

        print(f"\nChecking {total_emails} emails after readFilter='{read_filter}'...")
        print("-" * 70)

        for msg_id in latest_ids:
            processed_count += 1

            print(
                f"\n[{processed_count}/{total_emails}] "
                f"Processing email ID {msg_id.decode()}..."
            )

            is_read = is_message_read(mail, msg_id)

            if read_filter == "read" and not is_read:
                print(f"SAFETY SKIP: unread email ID {msg_id.decode()} while readFilter=read")
                continue

            if read_filter == "unread" and is_read:
                print(f"SAFETY SKIP: read email ID {msg_id.decode()} while readFilter=unread")
                continue

            status, msg_data = mail.uid("fetch", msg_id, "(BODY.PEEK[])")
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

                action, target_folder = resolve_action(rule, is_read)
                action_result = "DRY_RUN"

                if not dry_run and action == "move":
                    moved = move_email(mail, msg_id, target_folder)

                    if moved:
                        action_result = "MOVED"
                        move_success_count += 1
                    else:
                        action_result = "MOVE_FAILED"
                        move_failed_count += 1

                elif action == "skip":
                    action_result = "SKIPPED"

                else:
                    dry_run_count += 1

                print("MATCH")
                print(f"Read: {'YES' if is_read else 'NO'}")
                print(f"ID: {msg_id.decode()}")
                print(f"Rule: {rule['name']}")
                print(f"Target folder: {rule['folder']}")
                print(f"From: {sender}")
                print(f"Subject: {subject}")
                print(f"Match reason: {match_reason}")
                print(f"Action result: {action_result}")
                print("-" * 70)

                last_log_file = write_log_row({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "dry_run": dry_run,
                    "email_id": msg_id.decode(),
                    "read": "YES" if is_read else "NO",
                    "matched": "YES",
                    "rule": rule["name"],
                    "target_folder": target_folder,
                    "from": sender,
                    "subject": subject,
                    "action": action,
                    "action_result": action_result,
                    "match_reason": match_reason,
                })

            else:
                unmatched_count += 1

                if log_unmatched:
                    last_log_file = write_log_row({
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "dry_run": dry_run,
                        "email_id": msg_id.decode(),
                        "read": "YES" if is_read else "NO",
                        "matched": "NO",
                        "rule": "UNMATCHED",
                        "target_folder": "",
                        "from": sender,
                        "subject": subject,
                        "action": "none",
                        "action_result": "UNMATCHED",
                        "match_reason": "",
                    })

        if not dry_run:
            print("\nApplying deletions from INBOX...")
            mail.expunge()

        mail.logout()

    if last_log_file:
        print("\nLog file created:")
        print(last_log_file)
    else:
        print("\nNo log file created.")

    print("\nRun summary")
    print("-" * 40)
    print(f"Processed emails   : {processed_count}")
    print(f"Matched emails     : {matched_count}")
    print(f"Unmatched emails   : {unmatched_count}")
    print()
    print(f"Moved successfully : {move_success_count}")
    print(f"Move failed        : {move_failed_count}")
    print(f"Dry run actions    : {dry_run_count}")


if __name__ == "__main__":
    main()