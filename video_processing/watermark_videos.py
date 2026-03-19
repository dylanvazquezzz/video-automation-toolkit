"""
Video Watermark Script
Adds an image overlay to the bottom of all videos in a folder using ffmpeg
"""

import subprocess
import sys
import io
from pathlib import Path

# Fix Unicode output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Configuration
SCRIPT_DIR = Path(__file__).parent
MAIN_VIDEOS_DIR = SCRIPT_DIR / "main_videos"
WATERMARKS_DIR = SCRIPT_DIR / "watermarks"
FINISHED_DIR = SCRIPT_DIR / "finished"

# FFmpeg path
FFMPEG_PATH = r"ffmpeg"

# Supported formats
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def get_files(directory: Path, extensions: set) -> list[Path]:
    """Get all files with specified extensions from a directory."""
    if not directory.exists():
        return []
    return [f for f in directory.iterdir() if f.suffix.lower() in extensions]


def get_video_width(video: Path) -> int | None:
    """Get the width of a video using ffprobe."""
    ffprobe = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe")
    try:
        cmd = [
            ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width",
            "-of", "csv=p=0",
            str(video)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return None


def add_watermark(video: Path, watermark: Path, output: Path) -> bool:
    """Overlay the watermark image at the bottom of the video."""
    try:
        video_width = get_video_width(video)
        if not video_width:
            print("  Could not detect video width")
            return False

        # Scale watermark to 30% of video width, place a little above the bottom
        wm_width = int(video_width * 0.3)
        filter_complex = (
            f"[1:v]scale={wm_width}:-1[wm];"
            "[0:v][wm]overlay=(W-w)/2:H-h-H*0.05"
        )

        cmd = [
            FFMPEG_PATH, "-y",
            "-i", str(video),
            "-i", str(watermark),
            "-filter_complex", filter_complex,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600
        )

        if result.returncode != 0:
            print(f"  ffmpeg stderr: {result.stderr[-500:]}")

        return result.returncode == 0

    except Exception as e:
        print(f"  Watermark error: {e}")
        return False


def main():
    print("=" * 60)
    print("Video Watermark - Add Image to Bottom of Videos")
    print("=" * 60)

    # Ensure directories exist
    MAIN_VIDEOS_DIR.mkdir(exist_ok=True)
    WATERMARKS_DIR.mkdir(exist_ok=True)
    FINISHED_DIR.mkdir(exist_ok=True)

    # Get watermark image from watermarks folder
    watermarks = get_files(WATERMARKS_DIR, IMAGE_EXTENSIONS)

    if not watermarks:
        print(f"\nNo watermark images found in: {WATERMARKS_DIR}")
        print("Add your watermark image(s) to this folder and try again.")
        return

    watermark = watermarks[0]
    if len(watermarks) > 1:
        print(f"\nMultiple watermarks found, using: {watermark.name}")

    # Get all videos
    videos = get_files(MAIN_VIDEOS_DIR, VIDEO_EXTENSIONS)

    if not videos:
        print(f"\nNo videos found in: {MAIN_VIDEOS_DIR}")
        print("Add your videos to this folder and try again.")
        return

    # Limit to 289 videos
    MAX_VIDEOS = 289
    videos = videos[:MAX_VIDEOS]

    print(f"\nProcessing {len(videos)} video(s)")
    print(f"Watermark: {watermark.name}")
    print(f"Output folder: {FINISHED_DIR}")

    # Process each video
    successful = 0
    failed = 0

    for i, video in enumerate(videos, 1):
        print(f"\n[{i}/{len(videos)}] Processing: {video.name}")

        output_file = FINISHED_DIR / f"{video.stem}_wm.mp4"

        if add_watermark(video, watermark, output_file):
            successful += 1
            print(f"  SUCCESS: {output_file.name}")
        else:
            failed += 1
            print(f"  FAILED: {video.name}")

    # Summary
    print("\n" + "=" * 60)
    print("Processing Complete!")
    print(f"  Successful: {successful}")
    print(f"  Failed: {failed}")
    print(f"  Output: {FINISHED_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
