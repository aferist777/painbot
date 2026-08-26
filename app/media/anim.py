"""Prototype: animated frames captured deterministically from the browser.

Every animated template exposes window.render(t, duration) and paints itself for
that exact moment. The capture loop sets the time, takes the shot, moves on —
so a slow screenshot never smears the motion, unlike sleeping between frames.
Frames go straight into ffmpeg over a pipe; nothing touches the disk.
"""
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from app.config import FFMPEG, FRAME_FPS, FRAME_H, FRAME_W
from app.media.render import TEMPLATES, _render_thread, _browser_instance

log = logging.getLogger("painbot.anim")

ANIM_DIR = TEMPLATES / "anim"
# One source of truth for the frame rate: this used to be hardcoded and
# quietly overrode the config, so animated clips came out at 30 while the
# editor counted in 24.
DEFAULT_FPS = FRAME_FPS


def _capture_impl(
    fragment: str,
    out_path: Path,
    seconds: float,
    fps: int,
    wait_for: Optional[str],
    hold: float,
) -> Path:
    shell = (TEMPLATES / "anim.html").read_text(encoding="utf-8")
    html = (
        shell.replace("__W__", str(FRAME_W))
        .replace("__H__", str(FRAME_H))
        .replace("__BODY__", fragment)
    )
    scratch = TEMPLATES.parent / "_anim.html"
    scratch.write_text(html, encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = max(int(round(seconds * fps)), 1)
    page = _browser_instance().new_page(
        viewport={"width": FRAME_W, "height": FRAME_H}, device_scale_factor=1
    )
    # Motion only happens at the start of a beat; the rest of it is the same
    # picture while the voice keeps going. Capturing just the moving part and
    # freezing the last frame cuts render time by roughly five times.
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
    ]
    if hold > 0.05:
        args += ["-vf", f"tpad=stop_mode=clone:stop_duration={hold:.2f}"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(fps), str(out_path),
    ]
    encoder = subprocess.Popen(args, stdin=subprocess.PIPE)
    try:
        page.goto(scratch.as_uri(), wait_until="load")
        if wait_for:
            page.wait_for_selector(wait_for, timeout=15000)
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(300)  # let mermaid settle
        page.evaluate("window.prepare && window.prepare()")
        if not page.evaluate("window.ready && window.ready()"):
            raise RuntimeError("фрагмент не собрал таймлайн")

        for index in range(total):
            page.evaluate("window.seek", index / fps)
            encoder.stdin.write(page.screenshot(type="png"))
    finally:
        page.close()
        if encoder.stdin:
            encoder.stdin.close()
        encoder.wait()

    log.info("animated %s: %s frames", out_path.name, total)
    return out_path


MOTION_SECONDS = 1.6  # how long anything on screen actually moves


def capture(
    fragment_name: str,
    out_path: Path,
    seconds: float = 5.0,
    fps: int = DEFAULT_FPS,
    wait_for: Optional[str] = None,
    data: Optional[dict] = None,
    motion: Optional[float] = None,
) -> Path:
    """Render one animated clip of `seconds` total length.

    `data` is injected into the fragment as JSON, so one template serves every
    beat that uses it.
    """
    fragment = (ANIM_DIR / f"{fragment_name}.html").read_text(encoding="utf-8")
    fragment = fragment.replace(
        "__DATA__", json.dumps(data or {}, ensure_ascii=False)
    )
    move = min(motion if motion is not None else MOTION_SECONDS, seconds)
    hold = max(seconds - move, 0.0)
    return _render_thread().submit(
        _capture_impl, fragment, out_path, move, fps, wait_for, hold
    ).result()
