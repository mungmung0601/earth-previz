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
        raise RuntimeError("ffmpeg를 찾을 수 없습니다. 시스템에 ffmpeg를 설치하세요.")

    codec_map = {"h264": "libx264", "h265": "libx265"}
    if codec not in codec_map:
        raise ValueError("codec는 h264 또는 h265만 지원합니다.")

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
            "ffmpeg 인코딩에 실패했습니다.\n"
            f"command: {' '.join(command)}\n"
            f"stderr:\n{result.stderr}"
        )
