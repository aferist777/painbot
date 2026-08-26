"""Burned-in captions in ASS, built from the voice track's word timings.

Half the audience watches muted, so the words are part of the picture rather
than an optional subtitle track.
"""
from pathlib import Path
from typing import Any

from app.config import FRAME_H, FRAME_W

CHUNK_WORDS = 3          # how many words appear at once
MIN_CHUNK_SECONDS = 0.45

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: __W__
PlayResY: __H__
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,74,&H00FFFFFF,&H00101418,&H00000000,-1,0,0,0,100,100,0,0,1,7,3,2,90,90,300,204

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _stamp(seconds: float) -> str:
    seconds = max(seconds, 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _chunks(words: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for start in range(0, len(words), CHUNK_WORDS):
        group = words[start : start + CHUNK_WORDS]
        if not group:
            continue
        begin = float(group[0]["start"])
        end = max(float(group[-1]["end"]), begin + MIN_CHUNK_SECONDS)
        text = " ".join(str(word["word"]).strip() for word in group).strip()
        if text:
            out.append((begin, end, text))
    return out


def write_ass(words: list[dict[str, Any]], out_path: Path) -> Path:
    """One ASS file per beat; timings are relative to that beat's own audio."""
    header = HEADER.replace("__W__", str(FRAME_W)).replace("__H__", str(FRAME_H))
    lines = [header]
    for begin, end, text in _chunks(words or []):
        safe = text.replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(
            f"Dialogue: 0,{_stamp(begin)},{_stamp(end)},Cap,,0,0,0,,{safe}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
