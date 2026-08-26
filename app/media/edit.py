"""ffmpeg montage: one continuous voice track under a cut video line.

Clips carry no audio of their own. The voice is a single file laid over the
whole reel, so the rhythm between sentences is the one the engine produced,
not a sum of per-clip silences.
"""
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from app.admin.state import tune
from app.config import DATA_DIR, FFMPEG, FRAME_FPS, FRAME_H, FRAME_W
from app.db.repo import sget
from app.media import anim
from app.media.subs import write_ass

log = logging.getLogger("painbot.edit")

W, H, FPS = FRAME_W, FRAME_H, FRAME_FPS
ZOOM = 0.14
MUSIC_DIR = DATA_DIR / "music"
MUSIC_VOLUME = 0.22


def burn_subs() -> bool:
    """Off by default: captions are added in the publishing app."""
    return (sget("burn_subs") or "0") == "1"


def _run(args: list[str], label: str) -> None:
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg {label}: {(result.stderr or '')[-600:]}")


def _ken_burns(index: int, frames: int) -> str:
    span = max(frames - 1, 1)
    z = tune("video.zoom", ZOOM)
    if index % 4 == 0:
        zoom, x, y = f"1+{z}*on/{span}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif index % 4 == 1:
        zoom, x, y = f"{1 + z}-{z}*on/{span}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif index % 4 == 2:
        zoom = f"1+{z}*on/{span}"
        x = f"iw/2-(iw/zoom/2)+(iw*0.05)*(on/{span}-0.5)"
        y = "ih/2-(ih/zoom/2)"
    else:
        zoom = f"1+{z}*on/{span}"
        x = "iw/2-(iw/zoom/2)"
        y = f"ih/2-(ih/zoom/2)+(ih*0.05)*(on/{span}-0.5)"
    return f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS}"


def beat_clip(beat: dict[str, Any], out_path: Path, work_dir: Path) -> Path:
    """One silent clip, exactly as many frames as the beat owns.

    The frame count is authoritative, not the seconds: asking ffmpeg for 3.47s
    gets 104 frames back, and that missing fraction accumulates into visible
    drift between the picture and the voice.
    """
    frames_owned = int(beat.get("frames") or 0)
    if frames_owned <= 0:
        frames_owned = max(int(round(float(beat.get("seconds") or 1) * FPS)), 1)
    duration = frames_owned / FPS

    if beat.get("anim"):
        return anim.capture(
            beat["anim"]["fragment"],
            out_path,
            seconds=duration,
            data=beat["anim"].get("data"),
            motion=duration,  # continuous now: the frame never freezes
        )

    frame = beat.get("frame_path")
    if not frame or not Path(frame).exists():
        raise FileNotFoundError(f"нет кадра для бита {beat['idx'] + 1}")

    frames = max(frames_owned, 2)
    filters = [_ken_burns(beat["idx"], frames)]
    if burn_subs() and beat.get("words"):
        ass = write_ass(beat["words"], work_dir / f"sub-{beat['idx']:02d}.ass")
        filters.append("ass='" + str(ass).replace("\\", "/").replace(":", "\:") + "'")
    filters.append("format=yuv420p")

    _run(
        [
            "-loop", "1", "-framerate", str(FPS), "-i", str(frame),
            "-filter_complex", "[0:v]" + ",".join(filters) + "[v]",
            "-map", "[v]", "-frames:v", str(frames_owned),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", str(out_path),
        ],
        f"beat {beat['idx'] + 1}",
    )
    return out_path


def concat(clips: list[Path], out_path: Path, work_dir: Path) -> Path:
    listing = work_dir / "clips.txt"
    listing.write_text(
        "\n".join("file '" + str(c).replace("\\", "/") + "'" for c in clips),
        encoding="utf-8",
    )
    _run(
        ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out_path)],
        "concat",
    )
    return out_path


def find_music() -> Optional[Path]:
    if not MUSIC_DIR.exists():
        return None
    for pattern in ("*.mp3", "*.m4a", "*.wav"):
        found = sorted(MUSIC_DIR.glob(pattern))
        if found:
            return found[0]
    return None


def mux(video: Path, voice: Path, out_path: Path, music: Optional[Path]) -> Path:
    """Lay the single voice track over the cut video, music under it if present."""
    args = ["-i", str(video), "-i", str(voice)]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
        bed = tune("video.music_volume", MUSIC_VOLUME)
        args += [
            "-filter_complex",
            f"[2:a]volume={bed},aformat=sample_fmts=fltp:sample_rates=44100:"
            "channel_layouts=stereo[bed];"
            "[bed][1:a]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[duck];"
            "[duck][1:a]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
        ]
    else:
        args += ["-map", "0:v", "-map", "1:a"]
    args += [
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-shortest", str(out_path),
    ]
    _run(args, "mux")
    return out_path


def build(script_id: int, beats: list[dict], out_dir: Path, on_step=None) -> dict:
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    usable = [b for b in beats if b.get("frame_path") or b.get("anim")]
    if not usable:
        raise RuntimeError("нет ни одного бита с кадром")

    voice = out_dir / "voice.mp3"
    if not voice.exists():
        raise FileNotFoundError("нет голосовой дорожки — сначала собери кадры и озвучку")

    clips = []
    for position, beat in enumerate(usable, start=1):
        clips.append(beat_clip(beat, work / f"clip-{beat['idx']:02d}.mp4", work))
        if on_step:
            on_step(position, len(usable) + 1)

    silent = concat(clips, work / "joined.mp4", work)
    final = out_dir / f"reel-{script_id}.mp4"
    mux(silent, voice, final, find_music())
    if on_step:
        on_step(len(usable) + 1, len(usable) + 1)

    duration = sum(int(b.get("frames") or 0) for b in usable) / FPS
    music = find_music()
    return {
        "path": str(final),
        "size": final.stat().st_size,
        "duration": round(duration, 1),
        "clips": len(clips),
        "music": str(music) if music else "",
    }
