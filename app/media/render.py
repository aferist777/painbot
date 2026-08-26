"""Code-drawn visuals: HTML/CSS mockups and mermaid diagrams to PNG.

Diffusion models cannot draw a readable interface — labels come out as
gibberish and tables bend. For a startup breakdown the visuals are interfaces,
schemas and numbers, so they are rendered by a real browser instead. The same
engine later produces the frames for the reel.

Playwright's sync API refuses to run inside a live asyncio loop, so every entry
point here must be called through asyncio.to_thread.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("painbot.render")

TEMPLATES = Path(__file__).parent / "templates"
MERMAID = (Path(__file__).parent / "vendor" / "mermaid.min.js").resolve()

DEFAULT_WIDTH = 1000
SCALE = 2  # retina-ish; Telegram recompresses anyway, crisp text survives

# Playwright's sync API is bound to the thread that created it, and
# asyncio.to_thread hands out arbitrary pool threads. Everything that touches
# the browser is funnelled through one dedicated thread instead.
_executor: Optional[ThreadPoolExecutor] = None
_playwright = None
_browser = None


def _render_thread() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
    return _executor


def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return _render_thread().submit(fn, *args, **kwargs).result()


def _browser_instance():
    global _playwright, _browser
    if _browser is not None:
        return _browser
    from playwright.sync_api import sync_playwright

    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(args=["--force-color-profile=srgb"])
    log.info("chromium launched")
    return _browser


def _shutdown_impl() -> None:
    global _playwright, _browser
    if _browser is not None:
        _browser.close()
        _browser = None
    if _playwright is not None:
        _playwright.stop()
        _playwright = None


def shutdown() -> None:
    global _executor
    if _executor is None:
        return
    _run(_shutdown_impl)
    _executor.shutdown(wait=True)
    _executor = None


def _clip_of(page, selector: str, pad: int) -> Optional[dict]:
    """Mermaid pads its own canvas generously; crop to what is actually drawn."""
    box = page.eval_on_selector(
        selector,
        "el => { const r = el.getBoundingClientRect();"
        " return {x: r.x, y: r.y, width: r.width, height: r.height}; }",
    )
    if not box or box["width"] < 2 or box["height"] < 2:
        return None
    return {
        "x": max(box["x"] - pad, 0),
        "y": max(box["y"] - pad, 0),
        "width": box["width"] + pad * 2,
        "height": box["height"] + pad * 2,
    }


def _shoot(
    html: str,
    out_path: Path,
    width: int,
    wait_for: Optional[str] = None,
    fit: Optional[str] = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # A page created by set_content lives on about:blank and may not load
    # local scripts, so the markup goes to a real file next to the vendored
    # assets and is opened as file://.
    scratch = TEMPLATES.parent / "_render.html"
    scratch.write_text(html, encoding="utf-8")
    page = _browser_instance().new_page(
        viewport={"width": width, "height": 800}, device_scale_factor=SCALE
    )
    try:
        page.goto(scratch.as_uri(), wait_until="load")
        if wait_for:
            page.wait_for_selector(wait_for, timeout=15000)
        page.wait_for_timeout(250)  # let webfonts settle before the shot
        clip = _clip_of(page, fit, 44) if fit else None
        if clip:
            page.screenshot(path=str(out_path), clip=clip, full_page=True)
        else:
            element = page.query_selector("#root")
            (element or page).screenshot(path=str(out_path))
    finally:
        page.close()
    return out_path


def _render_mockup_impl(body_html: str, out_path: Path, width: int = DEFAULT_WIDTH,
                  columns: int = 2) -> Path:
    """body_html is a fragment written against the classes in shell.html."""
    shell = (TEMPLATES / "shell.html").read_text(encoding="utf-8")
    html = (
        shell.replace("__WIDTH__", str(width))
        .replace("__COLS__", str(max(columns, 1)))
        .replace("__BODY__", body_html)
    )
    return _shoot(html, out_path, width + 80)


def _render_diagram_impl(mermaid_code: str, out_path: Path, width: int = DEFAULT_WIDTH) -> Path:
    shell = (TEMPLATES / "diagram.html").read_text(encoding="utf-8")
    html = (
        shell.replace("__WIDTH__", str(width))
        .replace("__MERMAID__", "vendor/mermaid.min.js")
        .replace("__BODY__", mermaid_code)
    )
    return _shoot(html, out_path, width + 90, wait_for="#root svg", fit="#root svg g")


from app.config import FRAME_H, FRAME_W  # noqa: E402


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _render_frame_impl(
    out_path: Path,
    kind: str,
    body: str = "",
    caption: str = "",
    bg_path: Optional[Path] = None,
) -> Path:
    """One 9:16 reel frame. kind is title | mockup | diagram.

    Rendered at 1080x1920 with a 2x device scale, which leaves enough real
    pixels for the ken burns zoom in the edit to stay sharp.
    """
    shell = (TEMPLATES / "frame.html").read_text(encoding="utf-8")
    shell = shell.replace("__W__", str(FRAME_W)).replace("__H__", str(FRAME_H))

    bg_html = ""
    if bg_path is not None and Path(bg_path).exists():
        bg_html = f'<div class="bg"><img src="{Path(bg_path).resolve().as_uri()}"></div>'

    if kind == "title" or (not body and not bg_html):
        caption_html = f'<div class="cap solo"><h1>{_escape(caption)}</h1></div>'
        stage_html = ""
    else:
        caption_html = (
            f'<div class="cap"><h1>{_escape(caption)}</h1></div>' if caption else ""
        )
        inner = f'<pre class="mermaid">{body}</pre>' if kind == "diagram" else body
        stage_html = f'<div class="stage">{inner}</div>'

    html = (
        shell.replace("__BG__", bg_html)
        .replace("__CAPTION__", caption_html)
        .replace("__STAGE__", stage_html)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    scratch = TEMPLATES.parent / "_frame.html"
    scratch.write_text(html, encoding="utf-8")
    page = _browser_instance().new_page(
        viewport={"width": FRAME_W, "height": FRAME_H}, device_scale_factor=2
    )
    try:
        page.goto(scratch.as_uri(), wait_until="load")
        if kind == "diagram":
            page.wait_for_selector("#frame svg", timeout=15000)
        page.wait_for_timeout(300)
        page.evaluate("window.fitStage && window.fitStage()")
        page.wait_for_timeout(120)
        page.locator("#frame").screenshot(path=str(out_path))
    finally:
        page.close()
    return out_path


def render_mockup(body_html: str, out_path: Path, width: int = DEFAULT_WIDTH,
                  columns: int = 2) -> Path:
    return _run(_render_mockup_impl, body_html, out_path, width, columns)


def render_diagram(mermaid_code: str, out_path: Path, width: int = DEFAULT_WIDTH) -> Path:
    return _run(_render_diagram_impl, mermaid_code, out_path, width)


def render_frame(
    out_path: Path,
    kind: str,
    body: str = "",
    caption: str = "",
    bg_path: Optional[Path] = None,
) -> Path:
    return _run(_render_frame_impl, out_path, kind, body, caption, bg_path)
