# Yahoo Mail Organizer

A local Python-based email organizer for Yahoo Mail using IMAP.

This project scans emails from your Yahoo inbox, matches them against configurable rules, and optionally moves them into folders automatically.

The project was designed with a strong focus on:

* Safety
* Transparency
* Dry-run testing
* Logging
* Local execution
* Privacy

---

# Features

## Email Scanning

* Connects to Yahoo Mail via IMAP
* Supports:

  * Read emails only
  * Unread emails only
  * All emails

## Rule Engine

Rules are fully configurable using JSON.

Supported conditions:

* `requiredContains`
* `subjectContains`
* `fromContains`
* `bodyContains`

Supported matching modes:

* `matchType: any` (OR)
* `matchType: all` (AND)

## Safety Features

* Dry-run mode
* Read/unread validation safety checks
* Logging of all matched emails
* Optional logging of unmatched emails
* Warning before processing ALL emails

## Logging

CSV logs are generated automatically.

Logged information includes:

* Timestamp
* Rule name
* Match reason
* Action result
* Sender
* Subject
* Read/unread status

## Hebrew Support

Supports:

* Hebrew email content
* Hebrew folder names
* IMAP UTF-7 encoding for Yahoo folders

---

# Project Structure

```text
mail-organizer/
│
├── logs/
├── src/
│   ├── main.py
│   └── config.py
│
├── .env
├── .env.example
├── rules.json
├── rules.example.json
├── requirements.txt
└── README.md
```

---

# Installation

## Clone Repository

```bash
git clone <repository-url>
cd yahoo-mail-organizer
```

## Create Virtual Environment

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Install Dependencies

```bash
pip install -r requirements.txt
```

---

# Yahoo App Password

Yahoo requires an App Password for IMAP access.

1. Open Yahoo Account Security
2. Enable 2FA if needed
3. Create App Password
4. Use the generated password in `.env`

---

# Environment Variables

Create a `.env` file:

```env
YAHOO_EMAIL=your_email@yahoo.com
YAHOO_APP_PASSWORD=your_app_password
IMAP_SERVER=imap.mail.yahoo.com
IMAP_PORT=993
```

---

# Rules Configuration

Rules are stored in:

```text
rules.json
```

Example:

```json
{
  "dryRun": true,
  "limit": 20,
  "readFilter": "read",
  "logUnmatched": true,
  "rules": [
    {
      "name": "Etsy Sales",
      "enabled": true,
      "priority": 5,
      "folder": "Etsy/Sales",
      "matchType": "all",
      "requiredContains": [],
      "subjectContains": [
        "order confirmation"
      ],
      "fromContains": [
        "etsy"
      ],
      "bodyContains": [],
      "action": "move"
    }
  ]
}
```

---

# Matching Logic

## matchType = "any"

At least one condition group must match.

Example:

```text
subject match
OR
from match
OR
body match
```

## matchType = "all"

All configured condition groups must match.

Example:

```text
subject match
AND
from match
AND
body match
```

## requiredContains

All required keywords must appear somewhere in:

* Subject
* Sender
* Body

Useful for more precise classification.

---

# Running the Project

## Dry Run

Recommended for testing.

```json
"dryRun": true
```

Run:

```bash
python src/main.py
```

## Real Move Mode

```json
"dryRun": false
```

Start with a small limit first:

```json
"limit": 5
```

---

# Logs

Logs are created automatically under:

```text
logs/
```

Example:

```text
logs/mail_matches_2026-05-23.csv
```

---

# Recommended Workflow

1. Create simple rules first
2. Run in dry-run mode
3. Review CSV logs
4. Refine rules gradually
5. Move small batches first
6. Increase limits slowly

---

# Security Notes

* Never commit `.env`
* Never commit real `rules.json`
* Use `rules.example.json` for examples
* Revoke Yahoo App Password if exposed
* Prefer running locally over third-party automation services

---

# Current Limitations

* IMAP-based only
* Yahoo-specific testing
* No AI classification yet
* No GUI
* No incremental state tracking yet

---

# Future Ideas

Possible future improvements:

* Archive action
* Delete action
* Mark as read
* AI-assisted classification
* Rule statistics
* Duplicate suppression
* Scheduled execution
* Web dashboard
* Multiple rule files
* Gmail/Outlook support

---

# License

Currently intended for personal/private usage.

License can be added later if the project becomes open source.
