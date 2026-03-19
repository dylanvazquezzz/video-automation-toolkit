"""
Video Merger: Concat main_video + outro, replace audio with MP3.
- Upscales to 1080x1920 (9:16 vertical) at 30fps
- Uses NVENC hardware encoding for speed, falls back to libx264
- Processes videos in parallel
"""

import subprocess
import sys
import json
import random
import time
import threading
import concurrent.futures
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================
BASE_DIR = Path(__file__).parent
MAIN_VIDEOS_DIR = BASE_DIR / "main_videos"
OUTROS_DIR = BASE_DIR / "outros"
AUDIOS_DIR = BASE_DIR / "audios"
OUTPUT_DIR = BASE_DIR / "finished"

# Quality settings
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30

# Encoding: try NVENC first, fall back to libx264
USE_NVENC = True  # Set False to force CPU encoding
NVENC_PRESET = "p5"        # p1(fastest)..p7(best quality) — p5 is good balance
NVENC_CQ = 20              # Constant quality (lower = better, 18-23 good range)
X264_PRESET = "fast"       # ultrafast..veryslow
X264_CRF = 18              # Constant rate factor (lower = better)

WORKERS = 4  # Parallel encodes (lower if GPU runs out of memory)
# =============================================================================


def get_duration(filepath: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(filepath)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=15
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return 0.0


def check_nvenc() -> bool:
    """Test if NVENC actually works (driver + GPU must support it)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def build_encode_args(nvenc_available: bool) -> list[str]:
    """Return encoder-specific ffmpeg args."""
    if nvenc_available:
        return [
            "-c:v", "h264_nvenc",
            "-preset", NVENC_PRESET,
            "-rc", "vbr",
            "-cq", str(NVENC_CQ),
            "-b:v", "0",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
        ]
    else:
        return [
            "-c:v", "libx264",
            "-preset", X264_PRESET,
            "-crf", str(X264_CRF),
            "-tune", "film",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
        ]


def process_video(
    main_video: Path,
    outro: Path,
    audio: Path,
    output: Path,
    encode_args: list[str],
    print_lock: threading.Lock,
) -> str:
    """
    Process a single video:
    1. Scale main_video + outro to 1080x1920 @ 30fps
    2. Concat them
    3. Replace all audio with the MP3 (trimmed to total video length)
    Returns 'done', 'skipped', or 'failed'.
    """
    if output.exists():
        return "skipped"

    main_dur = get_duration(main_video)
    outro_dur = get_duration(outro)
    total_dur = main_dur + outro_dur

    if main_dur <= 0 or outro_dur <= 0:
        with print_lock:
            print(f"  SKIP (bad duration): {main_video.name[:60]}")
        return "failed"

    # filter_complex:
    # - Scale both inputs to target res, set fps, set SAR/pixel format
    # - Concat video streams only (we discard all original audio)
    # - Trim the MP3 to total duration
    scale_filter = (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:"
        f"force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={TARGET_FPS},setsar=1"
    )

    filter_complex = (
        f"[0:v]{scale_filter}[v0];"
        f"[1:v]{scale_filter}[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0[vout];"
        f"[2:a]atrim=0:{total_dur},asetpts=PTS-STARTPTS,afade=t=out:st={total_dur-0.5}:d=0.5[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(main_video),
        "-i", str(outro),
        "-i", str(audio),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        *encode_args,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(output),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=300
        )
        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            return "done"
        else:
            with print_lock:
                # Show last 200 chars of stderr for debugging
                err = (result.stderr or "")[-200:]
                print(f"  FAIL: {main_video.name[:50]}\n    {err}")
            if output.exists():
                output.unlink()
            return "failed"
    except subprocess.TimeoutExpired:
        with print_lock:
            print(f"  TIMEOUT: {main_video.name[:60]}")
        if output.exists():
            output.unlink()
        return "failed"
    except Exception as e:
        with print_lock:
            print(f"  ERROR: {main_video.name[:60]} — {e}")
        if output.exists():
            output.unlink()
        return "failed"


def main():
    print("=" * 60)
    print("Video Merger — Main + Outro + Audio Replacement")
    print("=" * 60)

    # Gather files
    main_videos = sorted(MAIN_VIDEOS_DIR.glob("*.mp4"))
    outros = sorted(OUTROS_DIR.glob("*.MP4")) + sorted(OUTROS_DIR.glob("*.mp4"))
    audios = sorted(AUDIOS_DIR.glob("*.mp3"))
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Main videos:  {len(main_videos)}")
    print(f"Outros:       {len(outros)}")
    print(f"Audios:       {len(audios)}")

    if not main_videos or not outros or not audios:
        print("ERROR: Missing files in one of the folders.")
        return

    # Check encoder
    nvenc_ok = USE_NVENC and check_nvenc()
    encoder_name = "NVENC (GPU)" if nvenc_ok else "libx264 (CPU)"
    encode_args = build_encode_args(nvenc_ok)
    print(f"Encoder:      {encoder_name}")
    print(f"Output:       {TARGET_WIDTH}x{TARGET_HEIGHT} @ {TARGET_FPS}fps")
    print(f"Workers:      {WORKERS}")
    print("=" * 60)

    # Test mode: process just the first video if --test flag
    test_mode = "--test" in sys.argv
    if test_mode:
        main_videos = main_videos[:1]
        print(f"TEST MODE: processing 1 video only\n")

    print_lock = threading.Lock()
    done = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    def worker(mv):
        outro = random.choice(outros)
        audio = random.choice(audios)
        out_path = OUTPUT_DIR / mv.name
        return process_video(mv, outro, audio, out_path, encode_args, print_lock)

    total = len(main_videos)

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(worker, mv): mv for mv in main_videos}

        for future in concurrent.futures.as_completed(futures):
            mv = futures[future]
            try:
                status = future.result()
            except Exception:
                status = "failed"

            if status == "done":
                done += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1

            processed = done + skipped + failed
            with print_lock:
                print(
                    f"  [{processed}/{total}] "
                    f"Done: {done} | Skipped: {skipped} | Failed: {failed}  ",
                    end="\r"
                )

    elapsed = time.time() - start_time
    print(f"\n\n{'=' * 60}")
    print(f"Finished in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Done:    {done}")
    print(f"  Skipped: {skipped} (already in finished/)")
    print(f"  Failed:  {failed}")
    print(f"  Output:  {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
