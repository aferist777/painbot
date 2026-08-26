"""Voice-over with word timings.

Edge TTS is free and reads Russian decently; ElevenLabs sounds noticeably more
alive but costs money. Both return the same shape, so the choice is one setting.

Timings matter beyond captions: the reel cuts frames on the real length of each
line, not on an estimate, so the picture never drifts from the voice.
"""
import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from app import config
from app.db.repo import sget

log = logging.getLogger("painbot.tts")

EDGE_VOICES = {"male": "ru-RU-DmitryNeural", "female": "ru-RU-SvetlanaNeural"}
ELEVEN_DEFAULT_MODEL = "eleven_v3"
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
ELEVEN_VOICES_URL = "https://api.elevenlabs.io/v1/voices"


def provider() -> str:
    return (sget("tts_provider") or "edge").lower()


def eleven_key() -> str:
    """Settings win over .env: the bot can be reconfigured without a restart."""
    return (sget("eleven_key") or config.ELEVENLABS_API_KEY or "").strip()


def eleven_model() -> str:
    return sget("eleven_model") or ELEVEN_DEFAULT_MODEL


def list_voices() -> list[dict[str, Any]]:
    key = eleven_key()
    if not key:
        raise RuntimeError("ключ ElevenLabs не задан")
    response = httpx.get(ELEVEN_VOICES_URL, headers={"xi-api-key": key}, timeout=30)
    response.raise_for_status()
    return [
        {
            "id": voice.get("voice_id"),
            "name": voice.get("name") or "",
            "labels": voice.get("labels") or {},
        }
        for voice in response.json().get("voices", [])
    ]


def check_key(key: str) -> tuple[bool, str]:
    """Verify a key before storing it, so a typo never reaches a render job."""
    try:
        response = httpx.get(
            ELEVEN_VOICES_URL, headers={"xi-api-key": key.strip()}, timeout=30
        )
    except httpx.HTTPError as exc:
        return False, str(exc)[:150]
    if response.status_code == 401:
        return False, "ключ отклонён"
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}"
    return True, str(len(response.json().get("voices", [])))


def edge_voice() -> str:
    """Always an Edge voice: the fallback path must not carry an ElevenLabs id."""
    return sget("edge_voice") or EDGE_VOICES["male"]


def voice_id() -> str:
    if provider() == "eleven":
        return sget("eleven_voice") or "21m00Tcm4TlvDq8ikWAM"
    return edge_voice()


def rate() -> str:
    """Edge accepts a prosody rate but largely ignores it for the Russian voices
    (+0% and +25% differ by a tenth of a second), so speed is applied afterwards
    with ffmpeg instead."""
    return sget("edge_rate") or "+0%"


def tempo() -> float:
    """Playback speed applied after synthesis. Reels read better fast."""
    try:
        return max(0.5, min(float(sget("voice_tempo") or 1.15), 2.0))
    except ValueError:
        return 1.15


def _speed_up(result: dict[str, Any], factor: float) -> dict[str, Any]:
    """atempo keeps the pitch; the word timings just divide by the factor."""
    if abs(factor - 1.0) < 0.02:
        return result
    import subprocess

    from app.config import FFMPEG

    source = Path(result["path"])
    fast = source.with_name(source.stem + "-fast" + source.suffix)
    done = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-filter:a", f"atempo={factor:.3f}", "-c:a", "libmp3lame", "-b:a", "128k",
         str(fast)],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        log.warning("atempo failed, keeping original speed: %s", done.stderr[-200:])
        return result
    fast.replace(source)
    for word in result["words"]:
        word["start"] = float(word["start"]) / factor
        word["end"] = float(word["end"]) / factor
    result["duration"] = round(float(result["duration"]) / factor, 3)
    return result


async def _edge(text: str, out_path: Path) -> dict[str, Any]:
    import edge_tts

    # edge-tts 7.x defaults to SentenceBoundary; word timings need this flag
    communicate = edge_tts.Communicate(
        text, edge_voice(), rate=rate(), boundary="WordBoundary"
    )
    words: list[dict[str, Any]] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("wb") as handle:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                handle.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                # edge reports 100-nanosecond ticks
                words.append(
                    {
                        "word": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                    }
                )

    duration = words[-1]["end"] if words else 0.0
    return {"path": str(out_path), "duration": round(duration, 3), "words": words}


async def _eleven(text: str, out_path: Path) -> dict[str, Any]:
    import base64
    import json

    key = eleven_key()
    if not key:
        raise RuntimeError(
            "Ключ ElevenLabs не задан. Настройки → Голос → Ключ, либо переключись на Edge."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=120) as http:
        response = await http.post(
            ELEVEN_URL.format(voice=voice_id()),
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": eleven_model(),
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
        )
        response.raise_for_status()
        payload = response.json()

    out_path.write_bytes(base64.b64decode(payload["audio_base64"]))

    # ElevenLabs aligns per character; fold it back into words for captions.
    alignment = payload.get("alignment") or {}
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[dict[str, Any]] = []
    current, start = "", None
    for index, char in enumerate(chars):
        if char.isspace():
            if current:
                words.append({"word": current, "start": start, "end": ends[index - 1]})
                current, start = "", None
            continue
        if not current:
            start = starts[index]
        current += char
    if current:
        words.append({"word": current, "start": start, "end": ends[-1] if ends else 0})

    duration = ends[-1] if ends else 0.0
    return {"path": str(out_path), "duration": round(float(duration), 3), "words": words}


async def speak(text: str, out_path: Path) -> dict[str, Any]:
    """Render one line. Returns path, real duration and word timings.

    A dead ElevenLabs key used to kill the whole render at the voicing step;
    now it falls back to Edge and says so, because a reel with the wrong voice
    beats no reel at all.
    """
    if provider() == "eleven":
        try:
            result = await _eleven(text, out_path)
        except Exception as exc:
            log.warning("ElevenLabs недоступен (%s), озвучиваю через Edge", str(exc)[:120])
            result = await _edge(text, out_path)
    else:
        result = await _edge(text, out_path)
    return _speed_up(result, tempo())


def speak_sync(text: str, out_path: Path) -> dict[str, Any]:
    return asyncio.run(speak(text, out_path))
