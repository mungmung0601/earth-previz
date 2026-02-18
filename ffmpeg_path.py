"""Resolve the ffmpeg binary path.

Priority: system ffmpeg > imageio-ffmpeg bundled binary.
"""
from __future__ import annotations

import shutil


def get_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path

    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    raise RuntimeError(
        "ffmpeg not found. Install it via:\n"
        "  pip install imageio[ffmpeg]\n"
        "or install FFmpeg on your system."
    )
