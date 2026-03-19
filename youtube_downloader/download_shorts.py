"""
YouTube Shorts Downloader
Downloads YouTube Shorts videos using yt-dlp
Supports individual video URLs and channel URLs
"""

import subprocess
import sys
import time
from pathlib import Path
import argparse
import concurrent.futures
import threading

# Configuration
SCRIPT_DIR = Path(__file__).parent
LINKS_FILE = SCRIPT_DIR / "links.txt"
DOWNLOAD_FOLDER = SCRIPT_DIR / "downloads"
ARCHIVE_FILE = SCRIPT_DIR / "downloaded.txt"
MIN_RESOLUTION = 1080  # Minimum height in pixels

# Dynamic timeout & early termination settings
SECONDS_PER_VIDEO = 15          # Estimated avg download time per short
BASE_TIMEOUT_SECONDS = 120      # Base overhead for yt-dlp startup
MAX_CONSECUTIVE_FAILURES = 20   # Abort threshold
FALLBACK_TIMEOUT_SECONDS = 7200 # 2-hour fallback if count query fails
SLEEP_INTERVAL = 3              # Seconds to sleep between downloads (avoid rate limit)
MAX_WORKERS = 4                 # Number of parallel downloads

def read_links(file_path: Path) -> list[str]:
    """Read YouTube links from the text file."""
    if not file_path.exists():
        print(f"Error: {file_path} not found!")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        links = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    return links

def is_channel_url(url: str) -> bool:
    """Check if URL is a channel/user URL."""
    channel_patterns = ["/@", "/channel/", "/user/", "/c/"]
    return any(pattern in url for pattern in channel_patterns)

def download_short(url: str, download_folder: Path, min_height: int = MIN_RESOLUTION, use_archive: bool = True) -> bool:
    """Download a single YouTube Short using yt-dlp with quality filter."""
    try:
        print(f"\nDownloading: {url}")

        # yt-dlp command with quality filter (1080p minimum)
        # Format: bestvideo with height >= min_height + bestaudio, fallback to best with height >= min_height
        format_string = f"bestvideo[height>={min_height}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height>={min_height}]+bestaudio/best[height>={min_height}]"

        cmd = [
            sys.executable, "-m", "yt_dlp",
            "-f", format_string,
            "--merge-output-format", "mp4",  # Ensure output is mp4
            "-o", str(download_folder / "%(title)s [%(id)s].%(ext)s"),  # Output template
            "--no-playlist",  # Don't download playlists
            "--no-warnings",  # Suppress warnings
        ]
        if use_archive:
            cmd.extend(["--download-archive", str(ARCHIVE_FILE)])
        cmd.append(url)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0:
            print("  Success!")
            return True
        else:
            error_msg = result.stderr.strip()
            # Check if error is due to quality requirements
            if "Requested format is not available" in error_msg or "no suitable formats" in error_msg.lower():
                print(f"  Skipped: No {min_height}p+ version available")
            else:
                print(f"  Error: {error_msg}")
            return False

    except subprocess.TimeoutExpired:
        print("  Timeout - download took too long")
        return False
    except Exception as e:
        print(f"  Error: {str(e)}")
        return False

def read_archive() -> set[str]:
    """Read the download archive and return a set of video IDs."""
    if not ARCHIVE_FILE.exists():
        return set()
    with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
        # yt-dlp archive format: "youtube VIDEO_ID" per line
        ids = set()
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                ids.add(parts[1])
        return ids

def get_channel_shorts_ids(channel_url: str) -> list[str]:
    """Query a channel and return the list of short video IDs."""
    try:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--flat-playlist",
            "--print", "id",
            "--match-filter", "duration < 61",
            channel_url + "/shorts"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()
        return []

    except (subprocess.TimeoutExpired, Exception):
        return []

def calculate_timeout(video_count: int) -> int:
    """Calculate a dynamic timeout based on the number of videos."""
    if video_count == 0:
        return FALLBACK_TIMEOUT_SECONDS

    timeout = BASE_TIMEOUT_SECONDS + (video_count * SECONDS_PER_VIDEO)
    # Clamp: min 5 minutes, max 24 hours
    timeout = max(300, min(timeout, 86400))
    return timeout

def _download_worker(
    video_id: str,
    download_folder: Path,
    min_height: int,
    archive_lock: threading.Lock,
    print_lock: threading.Lock,
    abort_event: threading.Event,
) -> str:
    """Worker function for parallel downloads. Returns 'downloaded', 'skipped', or 'failed'."""
    if abort_event.is_set():
        return "aborted"

    url = f"https://www.youtube.com/shorts/{video_id}"
    format_string = f"bestvideo[height>={min_height}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height>={min_height}]+bestaudio/best[height>={min_height}]"

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", format_string,
        "--merge-output-format", "mp4",
        "-o", str(download_folder / "%(channel)s - %(title)s [%(id)s].%(ext)s"),
        "--no-playlist",
        "--no-warnings",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if abort_event.is_set():
            return "aborted"

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = stdout + stderr

        if result.returncode == 0:
            # Write to archive under lock
            with archive_lock:
                with open(ARCHIVE_FILE, "a", encoding="utf-8") as f:
                    f.write(f"youtube {video_id}\n")
            return "downloaded"
        else:
            if "Requested format is not available" in combined or "no suitable formats" in combined.lower():
                return "skipped"
            else:
                with print_lock:
                    print(f"  ERROR [{video_id}]: {stderr.strip()[:200]}")
                return "failed"

    except subprocess.TimeoutExpired:
        with print_lock:
            print(f"  TIMEOUT [{video_id}]")
        return "failed"
    except Exception as e:
        with print_lock:
            print(f"  EXCEPTION [{video_id}]: {e}")
        return "failed"


def download_channel_shorts(
    channel_url: str,
    download_folder: Path,
    min_height: int = MIN_RESOLUTION,
    max_failures: int = MAX_CONSECUTIVE_FAILURES,
    workers: int = MAX_WORKERS,
) -> tuple[int, int]:
    """Download all shorts from a YouTube channel using parallel workers."""
    # Phase 1: Enumerate
    print(f"\nQuerying channel to count available shorts...")
    video_ids = get_channel_shorts_ids(channel_url)
    video_count = len(video_ids)

    if video_count == 0:
        print("Could not determine shorts count or channel has no shorts.")
        return (0, 0)

    archived = read_archive()
    already_done = sum(1 for vid in video_ids if vid in archived)
    remaining_ids = [vid for vid in video_ids if vid not in archived]
    remaining = len(remaining_ids)

    print(f"Found {video_count} shorts on channel.")
    if already_done > 0:
        print(f"  Already downloaded: {already_done} | Remaining: {remaining}")

    if remaining == 0:
        print("  Nothing new to download.")
        return (already_done, 0)

    timeout = calculate_timeout(remaining // max(workers, 1))
    print(f"Timeout set to {timeout // 60} minutes ({timeout}s)")
    print(f"Will abort after {max_failures} consecutive failures.")
    print(f"Workers: {workers}")
    print(f"Starting parallel download of {remaining} videos...\n")

    # Phase 2: Parallel download
    archive_lock = threading.Lock()
    print_lock = threading.Lock()
    abort_event = threading.Event()

    downloaded = 0
    skipped = 0
    failed = 0
    consecutive_failures = 0
    start_time = time.time()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for vid in remaining_ids:
                if abort_event.is_set():
                    break
                future = executor.submit(
                    _download_worker,
                    vid,
                    download_folder,
                    min_height,
                    archive_lock,
                    print_lock,
                    abort_event,
                )
                futures[future] = vid

            for future in concurrent.futures.as_completed(futures):
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    with print_lock:
                        print(f"\n  Timeout reached ({timeout}s). Aborting...")
                    abort_event.set()
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                vid = futures[future]
                try:
                    status = future.result()
                except Exception as e:
                    status = "failed"
                    with print_lock:
                        print(f"  FUTURE ERROR [{vid}]: {e}")

                if status == "aborted":
                    continue
                elif status == "downloaded":
                    downloaded += 1
                    consecutive_failures = 0
                elif status == "skipped":
                    skipped += 1
                    consecutive_failures = 0
                elif status == "failed":
                    failed += 1
                    consecutive_failures += 1

                    if consecutive_failures >= max_failures:
                        with print_lock:
                            print(f"\n  Aborting: {max_failures} consecutive failures reached.")
                        abort_event.set()
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                processed = downloaded + skipped + failed
                with print_lock:
                    print(f"  Progress: {processed}/{remaining} | {downloaded} downloaded | {skipped} skipped | {failed} failed", end="\r")

    except KeyboardInterrupt:
        print("\n\n  Ctrl+C received — shutting down gracefully...")
        abort_event.set()
        # In-progress downloads will finish naturally

    elapsed = time.time() - start_time
    print(f"\n\n  Channel download complete in {elapsed:.0f}s")
    print(f"  Downloaded: {downloaded} | Skipped: {skipped} | Failed: {failed}")
    return (downloaded, skipped + failed)

def main():
    parser = argparse.ArgumentParser(
        description="Download YouTube Shorts with quality filtering and channel support"
    )
    parser.add_argument(
        "--min-resolution",
        type=int,
        default=MIN_RESOLUTION,
        help=f"Minimum video height in pixels (default: {MIN_RESOLUTION})"
    )
    parser.add_argument(
        "--channel",
        type=str,
        help="Download shorts from a specific channel URL (alternative to links.txt)"
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=MAX_CONSECUTIVE_FAILURES,
        help=f"Abort after this many consecutive failures (default: {MAX_CONSECUTIVE_FAILURES})"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of parallel downloads (default: {MAX_WORKERS})"
    )
    args = parser.parse_args()

    print("=" * 50)
    print("YouTube Shorts Downloader (yt-dlp)")
    print(f"Minimum Resolution: {args.min_resolution}p")
    print("=" * 50)

    # Ensure download folder exists
    DOWNLOAD_FOLDER.mkdir(exist_ok=True)

    successful = 0
    failed = 0
    failed_links = []

    # If channel argument provided, download from that channel
    if args.channel:
        print(f"Saving to: {DOWNLOAD_FOLDER}")
        channel_success, channel_failed = download_channel_shorts(
            args.channel, DOWNLOAD_FOLDER, args.min_resolution, args.max_failures, args.workers
        )
        successful += channel_success
        failed += channel_failed
    else:
        # Read links from file
        links = read_links(LINKS_FILE)
        if not links:
            print("No links found in links.txt")
            print("\nTip: You can also use --channel <URL> to download from a channel")
            return

        print(f"Found {len(links)} link(s) to download")
        print(f"Saving to: {DOWNLOAD_FOLDER}")

        for i, link in enumerate(links, 1):
            print(f"\n[{i}/{len(links)}]", end="")

            # Check if it's a channel URL or individual video
            if is_channel_url(link):
                channel_success, channel_failed = download_channel_shorts(
                    link, DOWNLOAD_FOLDER, args.min_resolution, args.max_failures, args.workers
                )
                successful += channel_success
                failed += channel_failed
            else:
                if download_short(link, DOWNLOAD_FOLDER, args.min_resolution):
                    successful += 1
                else:
                    failed += 1
                    failed_links.append(link)

    print("\n" + "=" * 50)
    print("Download Complete!")
    print(f"  Successful: {successful}")
    print(f"  Failed/Skipped: {failed}")
    print(f"  Files saved to: {DOWNLOAD_FOLDER}")

    if failed_links:
        print("\nFailed/Skipped links:")
        for link in failed_links:
            print(f"  - {link}")

    print("=" * 50)

if __name__ == "__main__":
    main()
