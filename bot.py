from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from camera_path import generate_shot_plans
from encoder import encode_frames_to_mp4
from jsx_exporter import export_jsx
from kml_exporter import export_kml
from recommender import build_shot_analysis


RESOLUTION_PRESETS = {
    "270p": (480, 270),
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate coordinate-based cinematic previz and render as MP4."
    )
    parser.add_argument("--lat", type=float, default=None, help="Target latitude")
    parser.add_argument("--lng", type=float, default=None, help="Target longitude")
    parser.add_argument("--place", type=str, default=None, help="Place name/address (e.g., 'Manhattan H&M Building')")
    parser.add_argument("--esp", type=str, default=None, help="Earth Studio ESP/JSON file path")
    parser.add_argument("--shots", type=int, default=10, help="Number of shots to generate (max 10, ignored in ESP mode)")
    parser.add_argument("--duration-sec", type=int, default=300, help="Duration per shot (seconds), default 300s")
    parser.add_argument("--fps", type=int, default=24, help="Frame rate")
    parser.add_argument(
        "--resolution",
        choices=list(RESOLUTION_PRESETS),
        default="720p",
        help="Render resolution",
    )
    parser.add_argument(
        "--google-api-key",
        default=os.getenv("GOOGLE_MAPS_API_KEY", ""),
        help="Google Maps Tile API key (uses GOOGLE_MAPS_API_KEY env var if not provided)",
    )
    parser.add_argument("--codec", choices=["h264", "h265"], default="h264", help="Output codec")
    parser.add_argument("--output-dir", default="output", help="Output root directory")
    parser.add_argument("--dry-run", action="store_true", help="Generate metadata+ESP only, skip rendering")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frame limit for testing")

    ns = parser.parse_args()
    if ns.place:
        from geocoder import geocode
        lat, lng, display_name = geocode(ns.place, google_api_key=ns.google_api_key)
        ns.lat = lat
        ns.lng = lng
        print(f"[INFO] Place lookup: '{ns.place}' → {display_name}")
        print(f"[INFO] Coordinates: {lat}, {lng}")
    if ns.esp is None and (ns.lat is None or ns.lng is None):
        parser.error("One of --lat/--lng, --place, or --esp is required.")
    return ns


def _safe_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()

    width, height = RESOLUTION_PRESETS[args.resolution]
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.output_dir) / f"run_{run_id}"
    frames_root = run_dir / "frames"
    videos_root = run_dir / "videos"
    metadata_root = run_dir / "metadata"
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.esp:
        from esp_parser import parse_esp

        esp_path = Path(args.esp)
        print(f"[INFO] Loading ESP file: {esp_path}")
        shot, esp_meta = parse_esp(
            esp_path,
            fps=args.fps,
            target_lat=args.lat,
            target_lng=args.lng,
        )
        shot_plans = [shot]
        _safe_write_json(metadata_root / "esp_import_meta.json", esp_meta)
        print(f"[INFO] ESP import complete — frames: {esp_meta['total_source_frames']}, "
              f"target: ({esp_meta['target_lat']:.6f}, {esp_meta['target_lng']:.6f})")
    else:
        if args.shots < 1 or args.shots > 10:
            raise ValueError("--shots must be in the range 1-10.")
        shot_plans = generate_shot_plans(
            target_lat=args.lat,
            target_lng=args.lng,
            duration_sec=args.duration_sec,
            num_shots=args.shots,
        )

    summary_rows: list[dict] = []
    analyses: list[dict] = []

    print(f"[INFO] run_dir: {run_dir}")
    print(f"[INFO] shots: {len(shot_plans)} / duration: {args.duration_sec}s / resolution: {args.resolution}")

    kml_root = run_dir / "kml"
    jsx_root = run_dir / "jsx"

    for idx, shot in enumerate(shot_plans, start=1):
        print(f"[{idx}/{len(shot_plans)}] Analyzing {shot.shot_id}...")
        analysis = build_shot_analysis(shot)
        analyses.append(analysis)

        shot_meta_path = metadata_root / f"{shot.shot_id}.json"
        _safe_write_json(shot_meta_path, analysis)

        kml_path = kml_root / f"{shot.shot_id}.kml"
        export_kml(shot, kml_path, fps=2)

        jsx_path = jsx_root / f"{shot.shot_id}.jsx"
        export_jsx(shot, jsx_path, fps=args.fps, width=width, height=height)
        print(f"  - KML: {kml_path}")
        print(f"  - JSX: {jsx_path}")

        video_path = videos_root / f"{shot.shot_id}.mp4"
        rendered_frames = 0

        if args.dry_run:
            print(f"  - dry-run: skipping render ({shot.shot_id})")
        else:
            from renderer import RenderOptions, render_shot_frames

            frame_dir = frames_root / shot.shot_id
            print(f"  - Starting frame rendering...")
            rendered_frames = render_shot_frames(
                shot=shot,
                frame_dir=frame_dir,
                options=RenderOptions(
                    width=width,
                    height=height,
                    fps=args.fps,
                    google_api_key=args.google_api_key,
                    max_frames=args.max_frames,
                    headless=True,
                ),
            )
            print(f"  - Starting encoding... ({rendered_frames} frames)")
            encode_frames_to_mp4(frame_dir, video_path, fps=args.fps, codec=args.codec)
            print(f"  - Done: {video_path}")
            shutil.rmtree(frame_dir, ignore_errors=True)

        summary_rows.append(
            {
                "shot_id": shot.shot_id,
                "title": shot.title,
                "style": shot.style,
                "recommended_platform": analysis["recommendation"]["recommended_platform"],
                "confidence": analysis["recommendation"]["confidence"],
                "avg_speed_mps": round(analysis["motion"]["avg_speed_mps"], 3),
                "max_speed_mps": round(analysis["motion"]["max_speed_mps"], 3),
                "avg_altitude_m": round(analysis["motion"]["avg_altitude_m"], 2),
                "max_altitude_m": round(analysis["motion"]["max_altitude_m"], 2),
                "video_path": str(video_path) if not args.dry_run else "",
                "rendered_frames": rendered_frames,
            }
        )

    input_info: dict = {
        "shots": len(shot_plans),
        "duration_sec": args.duration_sec,
        "fps": args.fps,
        "resolution": args.resolution,
        "codec": args.codec,
        "dry_run": args.dry_run,
    }
    if args.esp:
        input_info["esp"] = args.esp
    if args.lat is not None:
        input_info["lat"] = args.lat
    if args.lng is not None:
        input_info["lng"] = args.lng

    summary_json = {
        "run_id": run_id,
        "input": input_info,
        "results": summary_rows,
    }
    _safe_write_json(metadata_root / "summary.json", summary_json)

    csv_path = metadata_root / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    print("[DONE] Generation complete")
    print(f"  - summary: {metadata_root / 'summary.json'}")
    print(f"  - summary_csv: {csv_path}")
    print(f"  - kml: {kml_root}")
    print(f"  - jsx: {jsx_root}")
    if not args.dry_run:
        print(f"  - videos: {videos_root}")


if __name__ == "__main__":
    main()
