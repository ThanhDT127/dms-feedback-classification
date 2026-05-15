"""
Configuration module — loads environment variables and defines constants/paths.
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env from service root ──────────────────────────────────────────────
_SERVICE_DIR = Path(__file__).resolve().parent
load_dotenv(_SERVICE_DIR / ".env")

# ── Logging setup ────────────────────────────────────────────────────────────
from logging_config import setup_logging

LOG_DIR = Path(os.environ.get("LOG_DIR", str(_SERVICE_DIR / "logs")))
setup_logging(log_dir=LOG_DIR)
logger = logging.getLogger("dms-watcher")

# ── Azure AD (Client Credentials) ───────────────────────────────────────────
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")

# ── Google Gemini / Vertex AI ────────────────────────────────────────────────
# Backend: "vertex" (default, uses GCP Service Account) or "apikey" (uses API key)
GEMINI_BACKEND = os.environ.get("GEMINI_BACKEND", "vertex").lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # only for apikey backend
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

# GCP / Vertex AI settings
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")
GCP_SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")

# ── SharePoint / Graph API ───────────────────────────────────────────────────
SHAREPOINT_DRIVE_ID = os.environ.get("SHAREPOINT_DRIVE_ID", "")
SHAREPOINT_ROOT_FOLDER_ID = os.environ.get("SHAREPOINT_ROOT_FOLDER_ID", "")
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]  # for client_credentials

# ── Polling ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))

# ── Notification ─────────────────────────────────────────────────────────────
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")

# Email sender — must be a real mailbox in your Azure AD tenant
# Used with /users/{sender}/sendMail (Client Credentials flow)
NOTIFICATION_SENDER_EMAIL = os.environ.get("NOTIFICATION_SENDER_EMAIL", "")

# Email recipients — comma-separated list (supports external addresses)
_raw_recipients = os.environ.get("NOTIFICATION_RECIPIENTS", "")
NOTIFICATION_RECIPIENTS: list[str] = [
    addr.strip() for addr in _raw_recipients.split(",") if addr.strip()
]

# Legacy fallback: if NOTIFICATION_RECIPIENTS is empty, fall back to NOTIFICATION_EMAIL
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "")
if not NOTIFICATION_RECIPIENTS and NOTIFICATION_EMAIL:
    NOTIFICATION_RECIPIENTS = [NOTIFICATION_EMAIL]


# ── Pipeline tuning ─────────────────────────────────────────────────────────
LLM_BATCH_SIZE = int(os.environ.get("LLM_BATCH_SIZE", "20"))
CKPT_EVERY = int(os.environ.get("CKPT_EVERY", "50"))
BASE_WAIT = 4.0       # seconds between retries
MAX_RETRY = 3         # max retry attempts for LLM calls
RATE_GAP_SEC = 4.0    # gap between RAG and Issue classifier to avoid 429
BM25_MIN_SCORE = 5.0  # minimum BM25 score threshold

# ── Data paths (inside container or local) ───────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_SERVICE_DIR.parent)))
KEYWORD_DIR = DATA_DIR / "Keyword"
MODEL_DIR = DATA_DIR / "Model"
WORK_DIR = Path(os.environ.get("WORK_DIR", str(_SERVICE_DIR.parent / "work")))
WORK_DIR.mkdir(parents=True, exist_ok=True)

# Key resource files
KW_MAP_PATH = KEYWORD_DIR / "kw_map.json"
DF_PRODUCTS_PATH = KEYWORD_DIR / "Phân Chia Nhóm Sản Phẩm V2.xlsx"

# ── Working files ────────────────────────────────────────────────────────────
SEEN_FILES_PATH = WORK_DIR / "seen_files.json"
HEALTH_FILE = WORK_DIR / "health.json"

# ── SharePoint folder names (expected structure) ─────────────────────────────
SP_INPUT_FOLDER = "Input"
SP_OUTPUT_FOLDER = "Output"
SP_CHECKPOINT_FOLDER = "Check_Point"
