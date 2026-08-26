import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "painbot.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Values the admin panel wrote, under the "cfg:" prefix. Read straight from the
# file with sqlite3: app.db.base imports this module, so it cannot be imported
# from here. Everything under this prefix is read once, at import — which is
# exactly why the panel calls these fields "применится после перезапуска".
CFG = "cfg:"


def _stored(name: str) -> str:
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return ""  # first run: no database file yet
    try:
        row = con.execute("SELECT value FROM settings WHERE key=?", (CFG + name,)).fetchone()
        return (row[0] or "").strip() if row else ""
    except sqlite3.Error:
        return ""  # database exists but has no schema yet
    finally:
        con.close()


def _env(name: str, default: str = "") -> str:
    """Panel first, then .env, then the built-in default."""
    return _stored(name) or (os.getenv(name) or default).strip()


TG_TOKEN = _env("TG_TOKEN")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
KIE_API_KEY = _env("KIE_API_KEY")
# kie.ai puts the model slug in the path: api.kie.ai/<model>/v1/chat/completions.
KIE_BASE = _env("KIE_BASE", "https://api.kie.ai/{model}/v1")

# Which backend the LLM layer talks to: "openrouter" or "anthropic".
# Defaults to whichever key is actually present.
LLM_PROVIDER = _env("LLM_PROVIDER") or (
    "kie" if KIE_API_KEY else "openrouter" if OPENROUTER_API_KEY else "anthropic"
)

_DEFAULT_MODELS = {
    "openrouter": {
        "screen": "google/gemini-3.7-flash",
        "ideate": "google/gemini-3.7-flash",
        "write": "google/gemini-3.7-flash",
    },
    "anthropic": {
        "screen": "claude-haiku-4-5",
        "ideate": "claude-sonnet-5",
        "write": "claude-opus-5",
    },
    "kie": {
        "screen": "gemini-3-7-flash-openai",
        "ideate": "gemini-3-7-flash-openai",
        "write": "gemini-3-7-flash-openai",
    },
}
_defaults = _DEFAULT_MODELS.get(LLM_PROVIDER, _DEFAULT_MODELS["openrouter"])
MODEL_SCREEN = _env("MODEL_SCREEN") or _defaults["screen"]
MODEL_IDEATE = _env("MODEL_IDEATE") or _defaults["ideate"]
MODEL_WRITE = _env("MODEL_WRITE") or _defaults["write"]
REPLICATE_API_TOKEN = _env("REPLICATE_API_TOKEN")
# Optional: only needed when the TTS switch is set to "eleven".
ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY")
# Optional: lifts GitHub search from 10 to 30 requests per minute.
GITHUB_TOKEN = _env("GITHUB_TOKEN")

R2_ACCOUNT_ID = _env("R2_ACCOUNT_ID")
R2_BUCKET = _env("R2_BUCKET")
R2_ACCESS_KEY_ID = _env("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = _env("R2_SECRET_ACCESS_KEY")
R2_PUBLIC_BASE = _env("R2_PUBLIC_BASE").rstrip("/")
# The bucket is shared with ugc-cg, so painbot keeps to its own prefix.
R2_PREFIX = _env("R2_PREFIX", "painbot/")

R2_READY = bool(R2_ACCOUNT_ID and R2_BUCKET and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY)

# Bundled so the project does not depend on another project's node_modules.
_bundled_ffmpeg = ROOT / "bin" / "ffmpeg.exe"
FFMPEG = _env("FFMPEG_PATH") or (str(_bundled_ffmpeg) if _bundled_ffmpeg.exists() else "ffmpeg")

# Reel geometry. Every module reads it from here: the browser canvas, the ASS
# subtitle coordinate space, the ffmpeg output and the Telegram video metadata.
FRAME_W = int(_env("FRAME_W", "1080"))
FRAME_H = int(_env("FRAME_H", "1920"))
# 24 rather than 30: a fifth fewer screenshots per reel, and the eye does
# not read the difference on a phone.
FRAME_FPS = int(_env("FRAME_FPS", "24"))

# Telegram refuses documents/videos above 50 MB from bots; stay under with a margin.
TG_UPLOAD_LIMIT = 48 * 1024 * 1024
