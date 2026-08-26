"""What a tunable value actually is, right now.

Every knob keeps its default written in code, next to the thing it affects. The
panel stores an override in the settings table and nothing else moves; delete
the override and the code default comes back untouched — that is the whole
"вернуть как было" mechanism.

Three namespaces, three different rules:

    tune:<name>   read at the moment it is used   — applies immediately
    cfg:<NAME>    read by app.config at import    — applies after a restart
    <name>        the keys that already existed   — left exactly where they were
"""
import json
from typing import Any, Optional

from app.db.repo import sdel, sget, sset

TUNE = "tune:"
CFG = "cfg:"


# ------------------------------------------------------------------ tunables


def tune(name: str, default: Any) -> Any:
    """The default decides the type; an override that will not cast is ignored."""
    raw = sget(TUNE + name)
    if raw is None or raw == "":
        return default
    try:
        if isinstance(default, bool):
            return raw not in ("0", "false", "False", "no")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
        if isinstance(default, (list, tuple)):
            value = json.loads(raw)
            return type(default)(value) if isinstance(value, list) else default
        return raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def set_tune(name: str, value: Any) -> None:
    if isinstance(value, (list, tuple)):
        sset(TUNE + name, json.dumps(list(value), ensure_ascii=False))
    elif isinstance(value, bool):
        sset(TUNE + name, "1" if value else "0")
    else:
        sset(TUNE + name, value)


def clear_tune(name: str) -> None:
    sdel(TUNE + name)


def tuned(name: str) -> bool:
    """True when the panel is overriding the code default."""
    return sget(TUNE + name) is not None


# -------------------------------------------------------------- config level


def cfg(name: str) -> str:
    return sget(CFG + name) or ""


def set_cfg(name: str, value: str) -> None:
    sset(CFG + name, value)


def clear_cfg(name: str) -> None:
    sdel(CFG + name)


# ------------------------------------------------------------------ secrets

# id -> (label, config attribute / env name, settings key it is stored under)
# ElevenLabs is the odd one out: its key already lived under "eleven_key" and
# is read live by the TTS layer, so it stays there and keeps applying at once.
SECRETS: dict[str, tuple[str, str, str]] = {
    "tg": ("Telegram", "TG_TOKEN", CFG + "TG_TOKEN"),
    "kie": ("kie.ai", "KIE_API_KEY", CFG + "KIE_API_KEY"),
    "openrouter": ("OpenRouter", "OPENROUTER_API_KEY", CFG + "OPENROUTER_API_KEY"),
    "anthropic": ("Anthropic", "ANTHROPIC_API_KEY", CFG + "ANTHROPIC_API_KEY"),
    "eleven": ("ElevenLabs", "ELEVENLABS_API_KEY", "eleven_key"),
    "replicate": ("Replicate", "REPLICATE_API_TOKEN", CFG + "REPLICATE_API_TOKEN"),
    "github": ("GitHub", "GITHUB_TOKEN", CFG + "GITHUB_TOKEN"),
    "r2_key": ("R2 access key", "R2_ACCESS_KEY_ID", CFG + "R2_ACCESS_KEY_ID"),
    "r2_secret": ("R2 secret", "R2_SECRET_ACCESS_KEY", CFG + "R2_SECRET_ACCESS_KEY"),
}

# Identifiers rather than secrets: shown in the open, edited like any other field.
OPEN_KEYS: dict[str, tuple[str, str]] = {
    "R2_ACCOUNT_ID": ("R2 account id", ""),
    "R2_BUCKET": ("R2 bucket", ""),
    "R2_PUBLIC_BASE": ("Публичный домен бакета", "пусто — ссылки на 7 дней"),
    "R2_PREFIX": ("Префикс в бакете", "painbot/"),
}


def secret(sid: str) -> str:
    """The live value: what the panel stored, else what .env brought in."""
    from app import config

    _, env_name, store = SECRETS[sid]
    return sget(store) or getattr(config, env_name, "") or ""


def set_secret(sid: str, value: str) -> None:
    sset(SECRETS[sid][2], value.strip())


def clear_secret(sid: str) -> None:
    sdel(SECRETS[sid][2])


def mask(value: str) -> Optional[str]:
    """What the browser is allowed to see: length and the last four characters."""
    if not value:
        return None
    tail = value[-4:] if len(value) > 8 else ""
    return "•" * min(len(value), 20) + (f" {tail}" if tail else "")
