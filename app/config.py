import os
from datetime import date
from decimal import Decimal

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./coalition_agents.db")
# Render / Heroku style URLs use postgres://; SQLAlchemy needs postgresql+psycopg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# The demo coalition is fictional and frozen at a fixed status date so every
# figure on the sandbox is reproducible. Override with STATUS_DATE=YYYY-MM-DD.
STATUS_DATE = date.fromisoformat(os.environ.get("STATUS_DATE", "2026-10-17"))

# Comma-separated origins allowed to call the API from a browser.
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "https://agilebykillette.com,https://www.agilebykillette.com"
).split(",") if o.strip()]


def _int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, default)).split("#", 1)[0].strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    v = None if v is None else v.split("#", 1)[0].strip()
    return default if not v else v.lower() in ("1", "true", "yes", "on")


def _dec(name: str, default: str) -> Decimal:
    raw = str(os.environ.get(name, default)).split("#", 1)[0].strip()
    try:
        return Decimal(raw or default)
    except Exception:
        return Decimal(default)


# --- Coalition legal profile (drives grant eligibility) ----------------------------
# The entity type is the single biggest factor in which grants the coalition can
# apply for. The demo assumes a 501(c)(3); change it here to see eligibility move.
# Allowed: NONPROFIT_501C3, COOPERATIVE, LLC, CORPORATION, FISCALLY_SPONSORED
COALITION_NAME = os.environ.get("COALITION_NAME", "Killette Industries Coalition")
COALITION_ENTITY_TYPE = os.environ.get("COALITION_ENTITY_TYPE", "NONPROFIT_501C3")
COALITION_SAM_REGISTERED = _bool("COALITION_SAM_REGISTERED", True)
# Negotiated or de minimis indirect cost rate, as a percentage of modified total direct costs.
COALITION_INDIRECT_RATE_PCT = _dec("COALITION_INDIRECT_RATE_PCT", "15")

# --- AI governance: limiter (all configurable via .env) ------------------------
RATE_LIMIT_PER_MINUTE = _int("RATE_LIMIT_PER_MINUTE", 5)        # AI calls per visitor per minute
DAILY_CAP_PER_VISITOR = _int("DAILY_CAP_PER_VISITOR", 25)       # AI calls per visitor per UTC day
DAILY_CAP_GLOBAL = _int("DAILY_CAP_GLOBAL", 200)                # AI calls, all visitors, per UTC day
DAILY_TOKEN_BUDGET = _int("DAILY_TOKEN_BUDGET", 400_000)        # input+output tokens, all visitors, per UTC day
MAX_TOKENS = _int("MAX_TOKENS", 1024)                           # max_tokens per agent model call
DRAFT_MAX_TOKENS = _int("DRAFT_MAX_TOKENS", 700)                # max_tokens for a grant-section or bio draft
MAX_AGENT_TURNS = _int("MAX_AGENT_TURNS", 6)                    # tool-use round trips per question
MAX_REQUEST_BYTES = _int("MAX_REQUEST_BYTES", 16_384)           # any request body
MAX_QUESTION_CHARS = _int("MAX_QUESTION_CHARS", 1000)           # agent question length
# 0 = use the socket peer address. Behind one proxy (Render) use 1.
TRUSTED_PROXY_HOPS = _int("TRUSTED_PROXY_HOPS", 0)
VISITOR_SALT = os.environ.get("VISITOR_SALT", "coalition-agents-demo")

# --- AI governance: kill switch -----------------------------------------------
# AI_ENABLED=false hard-disables every model call from the environment.
AI_ENABLED = _bool("AI_ENABLED", True)
# If set, POST /api/governance/kill-switch with header X-Admin-Token can flip
# the switch at runtime. If unset, the runtime switch is locked (env only).
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
# If set, every review decision and every approval must carry header X-Reviewer-Token.
REVIEWER_TOKEN = os.environ.get("REVIEWER_TOKEN", "")
