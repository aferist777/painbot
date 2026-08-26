"""Prompt files, read at the moment they are used.

They used to be read once at import, which meant every edit needed a restart.
The cache is keyed on the file's modification time, so a prompt saved from the
panel takes effect on the very next generation and costs one stat() per call.
"""
import logging
from pathlib import Path

log = logging.getLogger("painbot.prompts")

DIR = Path(__file__).parent / "prompts"

# name -> what it writes, for the panel
TITLES = {
    "screen": "Отбор болей",
    "ideate": "Идеи продукта",
    "article": "Разбор в канал",
    "script": "Сценарий ролика",
    "visual": "Картинки в разбор",
    "social": "Подпись и посты",
}

_cache: dict[str, tuple[float, str]] = {}


def path(name: str) -> Path:
    if name not in TITLES:
        raise ValueError(f"нет такого промпта: {name}")
    return DIR / f"{name}.md"


def load(name: str) -> str:
    file = path(name)
    stamp = file.stat().st_mtime
    hit = _cache.get(name)
    if hit is None or hit[0] != stamp:
        _cache[name] = (stamp, file.read_text(encoding="utf-8"))
    return _cache[name][1]


def save(name: str, text: str) -> None:
    """The previous version is kept next to it — that is the undo button."""
    file = path(name)
    if file.exists():
        file.with_suffix(".md.bak").write_text(
            file.read_text(encoding="utf-8"), encoding="utf-8"
        )
    file.write_text(text.replace("\r\n", "\n").strip() + "\n", encoding="utf-8")
    _cache.pop(name, None)
    log.info("промпт %s сохранён, %s символов", name, len(text))


def has_backup(name: str) -> bool:
    return path(name).with_suffix(".md.bak").exists()


def restore(name: str) -> bool:
    backup = path(name).with_suffix(".md.bak")
    if not backup.exists():
        return False
    save(name, backup.read_text(encoding="utf-8"))
    return True
