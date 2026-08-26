"""Reusable pipeline steps, shared by the single-button jobs and the full run.

Each step reports progress through a callback so the caller decides how the bar
is scaled — one stage of five, or the whole job.
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

from app.config import MEDIA_DIR
from app.db.base import q, q1, x
from app.db.repo import now
from app.llm.article import plan, visual_code
from app.llm.script import generate as generate_script
from app.llm.social import generate as generate_social
from app.llm.visual import make as make_visual
from app.media import storage
from app.media.edit import build as build_video
from app.media.render import render_diagram, render_frame, render_mockup
from app.media.tts import provider as tts_provider
from app.media.tts import speak

log = logging.getLogger("painbot.steps")

Tick = Callable[[float, float, str], Awaitable[None]]


async def _noop(done: float, total: float, note: str) -> None:
    return None


def _render_article_asset(article_id: int, block_idx: int) -> None:
    spec = visual_code(article_id, block_idx)
    out = MEDIA_DIR / "articles" / str(article_id) / f"block-{block_idx:02d}.png"
    if spec.kind == "diagram":
        render_diagram(spec.code, out)
    else:
        render_mockup(spec.code, out)
    x(
        "UPDATE article_assets SET path=?, status='done' "
        "WHERE article_id=? AND block_idx=?",
        str(out), article_id, block_idx,
    )


async def make_article(idea_id: int, tick: Tick = _noop) -> tuple[int, int, int]:
    """Returns (article_id, visuals done, visuals total)."""
    await tick(0, 1, "пишу текст разбора")
    article_id = await asyncio.to_thread(plan, idea_id, None)

    assets = q("SELECT block_idx FROM article_assets WHERE article_id=?", article_id)
    total = 1 + len(assets)
    await tick(1, total, f"текст готов, картинок: {len(assets)}")

    ok = 0
    for index, asset in enumerate(assets, start=1):
        try:
            await asyncio.to_thread(_render_article_asset, article_id, asset["block_idx"])
            ok += 1
        except Exception as exc:  # a broken picture must not sink the post
            log.warning("article visual %s failed: %s", asset["block_idx"], exc)
            x(
                "UPDATE article_assets SET status='failed' "
                "WHERE article_id=? AND block_idx=?",
                article_id, asset["block_idx"],
            )
        await tick(1 + index, total, f"картинка {index}/{len(assets)}")
    return article_id, ok, len(assets)


async def make_script(idea_id: int, tick: Tick = _noop) -> int:
    await tick(0, 2, "пишу сценарий ролика")
    script_id = await asyncio.to_thread(generate_script, idea_id, None)

    # Written here, not at delivery: by the time the cut is ready they are
    # already waiting, and they come from the same script the voice reads.
    await tick(1, 2, "подпись для инсты и посты в тредс")
    try:
        await asyncio.to_thread(generate_social, script_id, None)
    except Exception as exc:  # the reel is the product, its captions are not
        log.warning("script %s: тексты для соцсетей не собрались: %s", script_id, exc)
    return script_id


def _product(idea_id: int) -> dict:
    from app.llm.article import context as idea_context

    row = idea_context(idea_id)
    return {
        "продукт": row["name"],
        "one_liner": row["one_liner"],
        "стек": json.loads(row["stack_json"] or "[]"),
        "схема_бд": row["db_sketch"],
    }


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower(), flags=re.UNICODE).strip()


def _phrase_at(beat: dict, phrase: str) -> Optional[float]:
    """When inside this beat does the voice say that phrase, in seconds."""
    words = beat.get("words") or []
    tokens = _norm(phrase).split()
    if not tokens or not words:
        return None
    spoken = [_norm(w.get("word", "")) for w in words]
    for start in range(len(spoken) - len(tokens) + 1):
        if spoken[start : start + len(tokens)] == tokens:
            return round(float(words[start]["start"]) - float(beat["start"]), 2)
    return None


def _chips_of(beat: dict) -> list[dict]:
    chips = []
    for phrase in beat.get("keys") or []:
        at = _phrase_at(beat, phrase)
        if at is not None and at >= 0:
            chips.append({"text": phrase, "at": round(at, 2)})
    return sorted(chips, key=lambda c: c["at"])[:3]


HOST_DIR = Path(__file__).resolve().parent.parent / "media" / "host"
HOST_FILES = {"host_hello": "hello.jpg", "host_warn": "warn.jpg", "host_bye": "bye.jpg"}


def _steps_body(labels: list[str], active: int) -> str:
    """The enumeration as a picture: every step listed, the current one lit."""
    rows = []
    for index, label in enumerate(labels):
        state = "on" if index == active else ("done" if index < active else "")
        rows.append(
            f'<div class="step {state}"><div class="n">{index + 1}</div>'
            f"<div>{label}</div></div>"
        )
    return '<div class="steps">' + "".join(rows) + "</div>"


def _beat_words(beat: dict) -> list[dict]:
    """Word timings relative to the beat, so a fragment can move on the voice."""
    start = float(beat.get("start") or 0)
    return [
        {"word": w.get("word", ""), "at": round(float(w["start"]) - start, 3)}
        for w in (beat.get("words") or [])
        if float(w.get("start", 0)) >= start
    ]


CAPTION_SIDES = ("bottom", "top")


def _motion_data(beat: dict, script_id: int, extra: dict) -> dict:
    """Every fragment gets the same motion context: seed, words, length.

    The seed comes from the episode and the beat, so a reel keeps one character
    while its frames still differ, and a re-render reproduces it exactly.
    """
    data = dict(extra)
    data["seed"] = script_id * 1000 + beat["idx"] + 7
    data["beats"] = _beat_words(beat)
    data["duration"] = round(float(beat.get("seconds") or 4), 2)
    # alternate sides so the eye does not park in one spot for ninety seconds
    data.setdefault("caption_side", CAPTION_SIDES[beat["idx"] % 2])
    return data


def _draw_frame(
    beat: dict, product: dict, out_dir: Path, step_labels: list[str], script_id: int = 0
) -> tuple[str, str]:
    kind = beat["visual_kind"]
    frame = out_dir / f"frame-{beat['idx']:02d}.png"

    if kind in HOST_FILES:
        # The host is three fixed pictures, generated once and reused forever:
        # no drift between episodes and no cost per reel. The text over them is
        # animated, so even these frames are not stills.
        beat["anim"] = {
            "fragment": "host",
            "data": _motion_data(beat, script_id, {
                "image": (HOST_DIR / HOST_FILES[kind]).resolve().as_uri(),
                "badge": beat.get("badge", ""),
                "title": beat["on_screen"],
                "sub": beat.get("sub", ""),
                "captions": True,
            }),
        }
        return "", ""

    if kind == "steps":
        chips = _chips_of(beat)
        stage_html = ""
        brief = (beat.get("visual_brief") or "").strip()
        if brief:
            # what this step produces, drawn beside the progress strip
            try:
                spec = make_visual(
                    "mockup", brief, product, fmt="vertical",
                    on_screen=beat["on_screen"],
                )
                if spec.kind == "mockup":
                    stage_html = spec.code
            except Exception as exc:  # a missing picture is not a broken frame
                log.warning("step stage %s failed: %s", beat["idx"], exc)
        beat["anim"] = {
            "fragment": "steps",
            "data": _motion_data(beat, script_id, {
                "labels": step_labels,
                "active": beat.get("step_no", 0),
                "chips": chips,
                "stage_html": stage_html,
                "captions": not stage_html,
            }),
        }
        return "", stage_html

    if kind == "title":
        words = (beat["on_screen"] or beat["vo"][:60]).split()
        # the longest word carries the meaning; give it the accent colour
        accent = max(range(len(words)), key=lambda i: len(words[i])) if words else 0
        beat["anim"] = {
            "fragment": "title",
            "data": _motion_data(beat, script_id, {
                "words": words,
                "accent": accent,
                "badge": beat.get("badge", ""),
                "captions": beat.get("role") in ("bet", "warmup"),
                "mark": "underline" if beat.get("role") == "bet" else "circle",
            }),
        }
        return "", ""

    spec = make_visual(
        kind, beat["visual_brief"] or beat["vo"], product,
        fmt="vertical", on_screen=beat["on_screen"],
    )
    if spec.kind == "mockup":
        # whatever the model built gets revealed block by block instead of
        # sitting there as a poster
        beat["anim"] = {
            "fragment": "reveal",
            "data": _motion_data(beat, script_id, {
                "html": spec.code,
                "chips": _chips_of(beat),
                "captions": True,
            }),
        }
        return "", spec.code
    render_frame(frame, spec.kind, body=spec.code)
    return str(frame), spec.code


def _tok(text: str) -> str:
    return re.sub(r"[^0-9а-яёa-z]+", "", (text or "").lower())


def _split_words(beats: list[dict], words: list[dict]) -> None:
    """Assign the voice track's words to beats by MATCHING, not by counting.

    Counting tokens per beat looked fine until the engine tokenised something
    differently — a dash, a hyphenated word, a number — and every later beat
    inherited the shift, so the captions showed the previous line while the
    voice said the next one.
    """
    cursor = 0
    for beat in beats:
        expected = [_tok(w) for w in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", beat["vo"])]
        expected = [w for w in expected if w]
        taken: list[dict] = []
        i = 0
        while i < len(expected) and cursor < len(words):
            spoken = _tok(words[cursor].get("word", ""))
            if not spoken:
                cursor += 1
                continue
            if spoken == expected[i]:
                taken.append(words[cursor]); cursor += 1; i += 1
            elif spoken.startswith(expected[i]) or expected[i].startswith(spoken):
                # engine merged or split this one; take it and move both on
                taken.append(words[cursor]); cursor += 1; i += 1
            elif i + 1 < len(expected) and spoken == expected[i + 1]:
                i += 1  # the text has a token the voice never pronounced
            else:
                taken.append(words[cursor]); cursor += 1  # stray token, keep going
        beat["words"] = taken


def _map_words(beats: list[dict], words: list[dict], total: float) -> None:
    """Split one continuous voice track back into beats.

    The frames have to cut exactly where the next sentence starts. Voicing each
    beat separately used to give every seam the engine's own leading silence
    plus a tail — seven seconds of dead air across a reel.
    """
    _split_words(beats, words)
    for beat in beats:
        chunk = beat.get("words") or []
        beat["start"] = float(chunk[0]["start"]) if chunk else float(beat.get("start", total))
        beat["end"] = float(chunk[-1]["end"]) if chunk else total

    # No gaps, and every boundary snapped to the frame grid: ffmpeg can only cut
    # on whole frames.
    from app.config import FRAME_FPS

    for index, beat in enumerate(beats):
        beat["end"] = beats[index + 1]["start"] if index + 1 < len(beats) else total

    cursor = 0
    for index, beat in enumerate(beats):
        end_frame = round(float(beat["end"]) * FRAME_FPS)
        if index == len(beats) - 1:
            end_frame = max(end_frame, cursor + 1)
        frames = max(end_frame - cursor, 1)
        beat["start"] = round(cursor / FRAME_FPS, 4)
        beat["end"] = round((cursor + frames) / FRAME_FPS, 4)
        beat["frames"] = frames
        beat["seconds"] = round(frames / FRAME_FPS, 4)
        cursor += frames


async def make_assets(script_id: int, tick: Tick = _noop) -> dict:
    script = q1("SELECT * FROM scripts WHERE id=?", script_id)
    if script is None:
        raise ValueError(f"сценарий #{script_id} не найден")
    beats = json.loads(script["beats_json"] or "[]")
    product = _product(script["idea_id"])
    out_dir = MEDIA_DIR / "reels" / str(script_id)

    step_labels = [
        re.sub(r"^\s*\d+[.)]\s*", "", b["on_screen"] or b["vo"][:28])
        for b in beats
        if b.get("role") == "step"
    ]
    counter = 0
    for beat in beats:
        if beat.get("role") == "step":
            beat["step_no"] = counter
            counter += 1

    badge = f"Задание №{script['task_no'] or '?'} · {script['level'] or ''}".strip(" ·")
    for beat in beats:
        if beat["visual_kind"].startswith("host_"):
            beat["badge"] = badge
    first = next((b for b in beats if b["visual_kind"] == "host_hello"), None)
    if first:
        first["sub"] = "тренировка вайбкодинга"
    for beat in beats:
        if beat["visual_kind"] == "title":
            beat["badge"] = badge

    await tick(0, len(beats) + 1, "озвучиваю целиком")
    voice = out_dir / "voice.mp3"
    spoken = await speak(" ".join(b["vo"] for b in beats), voice)
    _map_words(beats, spoken["words"], spoken["duration"])
    await tick(1, len(beats) + 1, f"голос готов, {spoken['duration']:.0f} сек")

    for index, beat in enumerate(beats, start=1):
        try:
            frame_path, spec = await asyncio.to_thread(
                _draw_frame, beat, product, out_dir, step_labels, script_id
            )
            beat["frame_path"], status = frame_path, "done"
        except Exception as exc:  # one bad frame must not sink the reel
            log.warning("frame %s failed: %s", beat["idx"], exc)
            frame_path, spec, status = "", str(exc)[:300], "failed"
            beat["frame_path"] = ""
        x(
            "INSERT OR REPLACE INTO assets(script_id, beat_idx, kind, spec, provider, "
            "local_path, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
            script_id, beat["idx"], beat["visual_kind"], spec,
            "browser",
            frame_path, status, now(),
        )
        await tick(1 + index, len(beats) + 1, f"кадр {index}/{len(beats)}")

    x(
        "UPDATE scripts SET beats_json=?, duration_est=? WHERE id=?",
        json.dumps(beats, ensure_ascii=False), round(spoken["duration"], 1), script_id,
    )
    ready = sum(1 for b in beats if b.get("frame_path") or b.get("anim"))
    return {
        "beats": beats,
        "frames": ready,
        "total": len(beats),
        "duration": round(spoken["duration"], 1),
        "voice": tts_provider(),
        "voice_path": str(voice),
    }


async def make_video(script_id: int, tick: Tick = _noop) -> dict:
    script = q1("SELECT * FROM scripts WHERE id=?", script_id)
    beats = json.loads(script["beats_json"] or "[]")
    render_id = x(
        "INSERT INTO renders(script_id, status, created_at) VALUES(?, 'running', ?)",
        script_id, now(),
    )

    loop = asyncio.get_running_loop()

    def on_step(done: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(
            tick(done, total + 1, f"склейка {done}/{total}"), loop
        )

    try:
        result = await asyncio.to_thread(
            build_video, script_id, beats, MEDIA_DIR / "reels" / str(script_id), on_step
        )
    except Exception as exc:
        x("UPDATE renders SET status='failed', error=? WHERE id=?", str(exc)[:900], render_id)
        raise

    path = Path(result["path"])
    x(
        "UPDATE renders SET local_path=?, size_bytes=?, duration=?, status='done' WHERE id=?",
        str(path), result["size"], result["duration"], render_id,
    )

    link = ""
    if storage.ready():
        await tick(len(beats) + 1, len(beats) + 2, "выгружаю на R2")
        try:
            uploaded = await asyncio.to_thread(
                storage.upload, path, f"reels/{script_id}/{path.name}"
            )
            link = uploaded["url"]
            x(
                "UPDATE renders SET r2_key=?, public_url=?, uploaded_at=? WHERE id=?",
                uploaded["key"], link, now(), render_id,
            )
        except Exception as exc:  # a failed upload still leaves a local master
            log.warning("r2 upload failed: %s", exc)

    result["link"] = link
    return result
