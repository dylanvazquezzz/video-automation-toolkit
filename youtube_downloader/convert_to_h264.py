"""
Convert AV1/VP9 mp4 files to H.264 so they play in Windows default player.
Processes files in parallel and replaces originals.
"""

import subprocess
import sys
import io
from pathlib import Path
import concurrent.futures
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DOWNLOAD_FOLDER = Path(__file__).parent / "viral_downloads"
WORKERS = 4


def get_video_codec(filepath: Path) -> str:
    """Get the video codec of a file."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0",
             str(filepath)],
            capture_output=True, timeout=10, encoding="utf-8", errors="replace"
        )
        return result.stdout.strip()
    except Exception:
        return ""


def convert_file(filepath: Path, print_lock: threading.Lock) -> str:
    """Convert a single file from AV1/VP9 to H.264. Returns 'converted', 'skipped', or 'failed'."""
    codec = get_video_codec(filepath)

    if codec == "h264":
        return "skipped"

    if codec not in ("av1", "vp9", "vp8", "hevc"):
        return "skipped"

    temp_path = filepath.with_suffix(".tmp.mp4")

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(filepath),
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart",
             str(temp_path)],
            capture_output=True, timeout=300, encoding="utf-8", errors="replace"
        )

        if result.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 0:
            filepath.unlink()
            temp_path.rename(filepath)
            return "converted"
        else:
            if temp_path.exists():
                temp_path.unlink()
            with print_lock:
                print(f"    FAILED: {filepath.name[:60]} - {result.stderr.strip()[-100:]}")
            return "failed"

    except subprocess.TimeoutExpired:
        if temp_path.exists():
            temp_path.unlink()
        return "failed"
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        with print_lock:
            print(f"    ERROR: {filepath.name[:60]} - {e}")
        return "failed"


def main():
    # Collect all mp4 files
    files = list(DOWNLOAD_FOLDER.rglob("*.mp4"))
    print(f"Found {len(files)} mp4 files in {DOWNLOAD_FOLDER}\n")

    if not files:
        return

    print_lock = threading.Lock()
    converted = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(convert_file, f, print_lock): f for f in files}

        for future in concurrent.futures.as_completed(futures):
            try:
                status = future.result()
            except Exception:
                status = "failed"

            if status == "converted":
                converted += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1

            total = converted + skipped + failed
            with print_lock:
                print(f"  Progress: {total}/{len(files)} | "
                      f"Converted: {converted} | Already H.264: {skipped} | "
                      f"Failed: {failed}    ", end="\r")

    elapsed = time.time() - start_time
    print(f"\n\n  Done in {elapsed:.0f}s")
    print(f"  Converted: {converted} | Already H.264: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
