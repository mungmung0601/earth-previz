from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def encode_frames_to_mp4(
    frame_dir: Path,
    output_file: Path,
    *,
    fps: int,
    codec: str = "h264",
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg on your system.")

    codec_map = {"h264": "libx264", "h265": "libx265"}
    if codec not in codec_map:
        raise ValueError("Only h264 or h265 codecs are supported.")

    input_pattern = frame_dir / "frame_%06d.png"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    command = [
        ffmpeg,
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(input_pattern),
        "-c:v",
        codec_map[codec],
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "20",
        "-preset",
        "medium",
        str(output_file),
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg encoding failed.\n"
            f"command: {' '.join(command)}\n"
            f"stderr:\n{result.stderr}"
        )
