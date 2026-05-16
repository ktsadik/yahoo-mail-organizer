import os
from dotenv import load_dotenv

load_dotenv()


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


YAHOO_EMAIL = get_required_env("YAHOO_EMAIL")
YAHOO_APP_PASSWORD = get_required_env("YAHOO_APP_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.mail.yahoo.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"