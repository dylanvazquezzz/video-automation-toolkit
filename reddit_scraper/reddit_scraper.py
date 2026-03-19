"""
Reddit Content Scraper (No API Required)
Downloads videos from specified subreddits, filtered for Instagram Reels compatibility.

Setup:
1. Install dependencies: pip install requests
2. Requires FFmpeg installed (for aspect ratio checking)
3. Configure subreddits below
4. Run: python reddit_scraper.py (or double-click run_scraper.bat)
"""

import os
import re
import time
import subprocess
import json
import requests
from urllib.parse import urlparse
from pathlib import Path


# =============================================================================
# CONFIGURATION - Edit these values
# =============================================================================

# Subreddits to scrape (without the r/)
SUBREDDITS = [
    "MemeVideos"
    

    # Add more subreddits here
]

# Download settings
DOWNLOAD_FOLDER = r"V:\Scraping Tool\Scraped Videos"
POSTS_TO_FETCH = 500  # Total posts to fetch per subreddit (will paginate automatically)
SORT_BY = "top"  # Options: "hot", "new", "top", "rising"
TIME_FILTER = "all"  # For "top" sort: "hour", "day", "week", "month", "year", "all"

# Instagram Reels aspect ratio filter
FILTER_FOR_REELS = True  # Set to False to download all videos
MIN_ASPECT_RATIO = 9/16  # 0.5625 - vertical (9:16)
MAX_ASPECT_RATIO = 1.91  # landscape (1.91:1)

# Minimum resolution filter
MIN_VIDEO_HEIGHT = 720  # Minimum height in pixels (0 to disable) - skips low-quality videos

# File size limit and compression settings
MAX_FILE_SIZE_MB = 300  # Maximum file size in MB (videos larger will be compressed)
COMPRESS_OVERSIZED = True  # If True, compress videos over the limit instead of skipping
CRF_QUALITY = 18  # CRF value for compression (18-23 recommended, lower = better quality)

# Rate limiting
DELAY_BETWEEN_REQUESTS = 2  # seconds between API calls

# =============================================================================


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def safe_print(text):
    """Print text safely, handling Unicode errors on Windows console."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'ignore').decode('ascii'))


def get_file_extension(url):
    """Extract file extension from URL."""
    parsed = urlparse(url)
    path = parsed.path.lower()

    for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
        if ext in path:
            return ext

    for ext in ['.mp4', '.webm', '.mov', '.avi', '.gifv']:
        if ext in path:
            return '.mp4' if ext == '.gifv' else ext

    return None


def is_video_url(url):
    """Check if URL points to a video file."""
    url_lower = url.lower()
    video_extensions = ['.mp4', '.webm', '.gifv', '.mov']
    return any(ext in url_lower for ext in video_extensions)


def sanitize_filename(filename):
    """Remove invalid characters from filename."""
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Remove emojis and non-ASCII characters
    filename = filename.encode('ascii', 'ignore').decode('ascii')
    return filename.strip()[:200]


def get_video_dimensions(filepath):
    """Get video width and height using ffprobe."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-select_streams', 'v:0', str(filepath)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)

        if data.get('streams'):
            stream = data['streams'][0]
            width = stream.get('width', 0)
            height = stream.get('height', 0)
            if width and height:
                return width, height
    except Exception as e:
        safe_print(f"  Warning: Could not get video dimensions: {e}")
    return None, None


def get_video_aspect_ratio(filepath):
    """Get video aspect ratio using ffprobe."""
    width, height = get_video_dimensions(filepath)
    if width and height:
        return width / height
    return None


def is_valid_aspect_ratio(aspect_ratio):
    """Check if aspect ratio is valid for Instagram Reels."""
    if aspect_ratio is None:
        return False
    return MIN_ASPECT_RATIO <= aspect_ratio <= MAX_ASPECT_RATIO


def get_video_duration(filepath):
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', str(filepath)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        duration = float(data.get('format', {}).get('duration', 0))
        return duration
    except Exception as e:
        safe_print(f"    Warning: Could not get duration: {e}")
        return None


def compress_video_crf(input_path, output_path, crf=18):
    """
    Compress video using CRF (Constant Rate Factor) mode.
    CRF 18 is visually lossless, 23 is default, lower = better quality.
    """
    try:
        safe_print(f"    Compressing with CRF {crf}...")
        cmd = [
            'ffmpeg', '-y', '-i', str(input_path),
            '-c:v', 'libx264', '-crf', str(crf), '-preset', 'medium',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',  # Optimize for web streaming
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)

        if result.returncode == 0 and output_path.exists():
            new_size_mb = output_path.stat().st_size / (1024 * 1024)
            safe_print(f"    CRF compression complete: {new_size_mb:.1f} MB")
            return True
        else:
            safe_print(f"    CRF compression failed")
            return False
    except Exception as e:
        safe_print(f"    CRF compression error: {e}")
        return False


def compress_video_target_size(input_path, output_path, target_size_mb):
    """
    Compress video using two-pass encoding to hit a specific file size.
    This ensures we get under the size limit while maximizing quality.
    """
    duration = get_video_duration(input_path)
    if not duration or duration <= 0:
        safe_print(f"    Cannot determine duration for two-pass encoding")
        return False

    # Calculate target bitrate (in kbps)
    # target_size_mb * 8 * 1024 = target size in kilobits
    # Subtract ~128kbps for audio, leave some margin
    target_size_kb = target_size_mb * 8 * 1024
    audio_bitrate_kb = 128 * duration  # Audio takes ~128kbps
    video_bitrate_kb = target_size_kb - audio_bitrate_kb
    video_bitrate_kbps = int(video_bitrate_kb / duration * 0.95)  # 5% margin

    if video_bitrate_kbps < 500:
        safe_print(f"    Video too long for target size, bitrate would be too low")
        return False

    safe_print(f"    Two-pass encoding at {video_bitrate_kbps} kbps for {target_size_mb} MB target...")

    try:
        # Pass 1 - Analysis
        cmd_pass1 = [
            'ffmpeg', '-y', '-i', str(input_path),
            '-c:v', 'libx264', '-b:v', f'{video_bitrate_kbps}k',
            '-preset', 'medium', '-pass', '1',
            '-an', '-f', 'null',
            'NUL' if os.name == 'nt' else '/dev/null'
        ]
        result1 = subprocess.run(cmd_pass1, capture_output=True, timeout=600)

        if result1.returncode != 0:
            safe_print(f"    Two-pass encoding (pass 1) failed")
            return False

        # Pass 2 - Encoding
        cmd_pass2 = [
            'ffmpeg', '-y', '-i', str(input_path),
            '-c:v', 'libx264', '-b:v', f'{video_bitrate_kbps}k',
            '-preset', 'medium', '-pass', '2',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            str(output_path)
        ]
        result2 = subprocess.run(cmd_pass2, capture_output=True, timeout=600)

        # Clean up pass log files
        for f in Path('.').glob('ffmpeg2pass-*'):
            try:
                f.unlink()
            except:
                pass

        if result2.returncode == 0 and output_path.exists():
            new_size_mb = output_path.stat().st_size / (1024 * 1024)
            safe_print(f"    Two-pass compression complete: {new_size_mb:.1f} MB")
            return True
        else:
            safe_print(f"    Two-pass encoding (pass 2) failed")
            return False

    except Exception as e:
        safe_print(f"    Two-pass encoding error: {e}")
        # Clean up pass log files on error
        for f in Path('.').glob('ffmpeg2pass-*'):
            try:
                f.unlink()
            except:
                pass
        return False


def compress_video_if_needed(filepath, temp_folder, max_size_mb):
    """
    Compress video if it exceeds the size limit.
    First tries CRF compression, then falls back to two-pass for target size.
    Returns the path to the final file (original or compressed).
    """
    file_size_mb = filepath.stat().st_size / (1024 * 1024)

    if file_size_mb <= max_size_mb:
        safe_print(f"    File size {file_size_mb:.1f} MB OK")
        return filepath

    safe_print(f"    File size {file_size_mb:.1f} MB exceeds {max_size_mb} MB limit")

    # Try CRF compression first
    compressed_path = temp_folder / f"compressed_{filepath.name}"

    if compress_video_crf(filepath, compressed_path, CRF_QUALITY):
        new_size_mb = compressed_path.stat().st_size / (1024 * 1024)

        if new_size_mb <= max_size_mb:
            # CRF worked, replace original with compressed
            filepath.unlink()
            compressed_path.rename(filepath)
            safe_print(f"    Compression successful: {file_size_mb:.1f} MB -> {new_size_mb:.1f} MB")
            return filepath
        else:
            # CRF wasn't enough, try two-pass with target size
            safe_print(f"    CRF result {new_size_mb:.1f} MB still over limit, trying two-pass...")
            compressed_path.unlink()  # Remove CRF attempt

            target_size = max_size_mb - 1  # Target 1MB under limit for safety
            if compress_video_target_size(filepath, compressed_path, target_size):
                new_size_mb = compressed_path.stat().st_size / (1024 * 1024)
                filepath.unlink()
                compressed_path.rename(filepath)
                safe_print(f"    Two-pass successful: {file_size_mb:.1f} MB -> {new_size_mb:.1f} MB")
                return filepath
            else:
                safe_print(f"    Compression failed, keeping original")
                return None
    else:
        safe_print(f"    Compression failed")
        return None


def download_file(url, filepath):
    """Download a file from URL to filepath."""
    try:
        headers = {'User-Agent': USER_AGENT}
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        response.raise_for_status()

        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        safe_print(f"  Error downloading: {e}")
        return False


def download_reddit_video_with_audio(video_url, filepath, temp_folder):
    """Download Reddit video and audio separately, then merge with FFmpeg."""
    # Reddit video URLs look like: https://v.redd.it/xyz/DASH_720.mp4
    # Audio is at: https://v.redd.it/xyz/DASH_AUDIO_128.mp4 (or DASH_audio.mp4)

    video_temp = temp_folder / "temp_video.mp4"
    audio_temp = temp_folder / "temp_audio.mp4"

    # Download video
    if not download_file(video_url, video_temp):
        return False

    # Try to get audio URL by replacing the video quality with audio
    base_url = video_url.rsplit('/', 1)[0]
    audio_urls = [
        f"{base_url}/DASH_AUDIO_128.mp4",
        f"{base_url}/DASH_AUDIO_64.mp4",
        f"{base_url}/DASH_audio.mp4",
        f"{base_url}/audio.mp4",
    ]

    audio_downloaded = False
    for audio_url in audio_urls:
        try:
            headers = {'User-Agent': USER_AGENT}
            response = requests.head(audio_url, headers=headers, timeout=10)
            if response.status_code == 200:
                if download_file(audio_url, audio_temp):
                    audio_downloaded = True
                    break
        except:
            continue

    if audio_downloaded:
        # Merge video and audio with FFmpeg
        try:
            cmd = [
                'ffmpeg', '-y', '-i', str(video_temp), '-i', str(audio_temp),
                '-c:v', 'copy', '-c:a', 'aac', '-strict', 'experimental',
                str(filepath)
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)

            # Clean up temp files
            if video_temp.exists():
                video_temp.unlink()
            if audio_temp.exists():
                audio_temp.unlink()

            if result.returncode == 0:
                safe_print(f"    Merged video with audio")
                return True
            else:
                safe_print(f"    FFmpeg merge failed, using video only")
                # Fall back to video-only
                if not download_file(video_url, filepath):
                    return False
                return True
        except Exception as e:
            safe_print(f"    Error merging: {e}, using video only")
            # Clean up and fall back
            if video_temp.exists():
                video_temp.unlink()
            if audio_temp.exists():
                audio_temp.unlink()
            if not download_file(video_url, filepath):
                return False
            return True
    else:
        # No audio available, just use the video
        safe_print(f"    No audio stream found, video only")
        video_temp.rename(filepath)
        return True


def fetch_subreddit_json(subreddit_name, sort_by, time_filter, limit, after=None):
    """Fetch subreddit data from Reddit's public JSON endpoint."""
    if sort_by == "top":
        url = f"https://www.reddit.com/r/{subreddit_name}/top.json?t={time_filter}&limit={limit}"
    else:
        url = f"https://www.reddit.com/r/{subreddit_name}/{sort_by}.json?limit={limit}"

    if after:
        url += f"&after={after}"

    headers = {'User-Agent': USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        safe_print(f"Error fetching subreddit data: {e}")
        return None


def fetch_all_posts(subreddit_name, sort_by, time_filter, total_posts):
    """Fetch multiple pages of posts from a subreddit."""
    all_posts = []
    after = None

    while len(all_posts) < total_posts:
        # Reddit limits to 100 per request
        limit = min(100, total_posts - len(all_posts))

        safe_print(f"  Fetching posts {len(all_posts)+1}-{len(all_posts)+limit}...")
        json_data = fetch_subreddit_json(subreddit_name, sort_by, time_filter, limit, after)

        if not json_data:
            break

        posts = json_data.get('data', {}).get('children', [])
        if not posts:
            break

        all_posts.extend(posts)

        # Get the "after" token for pagination
        after = json_data.get('data', {}).get('after')
        if not after:
            break  # No more pages

        time.sleep(DELAY_BETWEEN_REQUESTS)

    return all_posts


def process_post(post_data, subreddit_folder, temp_folder):
    """Process a single post and download video if it meets criteria."""
    data = post_data.get('data', {})
    title = sanitize_filename(data.get('title', 'untitled'))
    post_id = data.get('id', 'unknown')
    url = data.get('url', '')

    video_url = None
    is_reddit_video = False
    reddit_video_height = 0

    # Check for Reddit-hosted video
    if data.get('is_video'):
        media = data.get('media', {})
        if media:
            reddit_video = media.get('reddit_video', {})
            video_url = reddit_video.get('fallback_url')
            reddit_video_height = reddit_video.get('height', 0)
            is_reddit_video = True

    # Check for direct video links
    elif is_video_url(url):
        if '.gifv' in url.lower():
            video_url = url.replace('.gifv', '.mp4')
        else:
            video_url = url

    if not video_url:
        return False

    # Pre-download resolution check for Reddit-hosted videos (uses API metadata, no download needed)
    if is_reddit_video and MIN_VIDEO_HEIGHT > 0 and reddit_video_height:
        if reddit_video_height < MIN_VIDEO_HEIGHT:
            safe_print(f"  Skipping (low res {reddit_video_height}p): {title[:60]}")
            return False

    filename = f"{post_id}_{title}.mp4"
    final_path = subreddit_folder / filename

    # Skip if already exists
    if final_path.exists():
        safe_print(f"  Skipping (exists): {filename}")
        return False

    # Download to temp folder first
    temp_path = temp_folder / filename

    safe_print(f"  Downloading: {filename}")

    # Use special handling for Reddit-hosted videos (separate audio stream)
    if is_reddit_video:
        if not download_reddit_video_with_audio(video_url, temp_path, temp_folder):
            return False
    else:
        if not download_file(video_url, temp_path):
            return False

    # Check file size and compress if needed
    if MAX_FILE_SIZE_MB > 0:
        file_size_mb = temp_path.stat().st_size / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            if COMPRESS_OVERSIZED:
                result = compress_video_if_needed(temp_path, temp_folder, MAX_FILE_SIZE_MB)
                if result is None:
                    safe_print(f"    Could not compress to under {MAX_FILE_SIZE_MB} MB, skipping")
                    if temp_path.exists():
                        temp_path.unlink()
                    return False
            else:
                safe_print(f"    File size {file_size_mb:.1f} MB exceeds limit ({MAX_FILE_SIZE_MB} MB), skipping")
                temp_path.unlink()
                return False
        else:
            safe_print(f"    File size {file_size_mb:.1f} MB OK")

    # Get video dimensions if needed for resolution or aspect ratio checks
    width, height = None, None
    need_dimensions = (MIN_VIDEO_HEIGHT > 0 and not is_reddit_video) or FILTER_FOR_REELS
    if need_dimensions:
        width, height = get_video_dimensions(temp_path)

    # Post-download resolution check (for non-Reddit videos; Reddit was pre-checked via metadata)
    if MIN_VIDEO_HEIGHT > 0 and not is_reddit_video:
        if width is not None and height is not None:
            if height < MIN_VIDEO_HEIGHT:
                safe_print(f"    Resolution {width}x{height} below minimum ({MIN_VIDEO_HEIGHT}p), deleting")
                temp_path.unlink()
                return False
            else:
                safe_print(f"    Resolution {width}x{height} OK")
        else:
            safe_print(f"    Could not determine resolution, keeping video")

    # Check aspect ratio if filtering is enabled
    if FILTER_FOR_REELS:
        aspect_ratio = (width / height) if (width and height) else None

        if aspect_ratio is None:
            safe_print(f"    Could not determine aspect ratio, keeping video")
            temp_path.rename(final_path)
            return True

        if is_valid_aspect_ratio(aspect_ratio):
            safe_print(f"    Aspect ratio {aspect_ratio:.2f} OK (Reels compatible)")
            temp_path.rename(final_path)
            return True
        else:
            safe_print(f"    Aspect ratio {aspect_ratio:.2f} not suitable for Reels, deleting")
            temp_path.unlink()
            return False

    temp_path.rename(final_path)
    return True


def scrape_subreddit(subreddit_name, base_folder):
    """Scrape videos from a subreddit."""
    print(f"\n{'='*60}")
    print(f"Scraping r/{subreddit_name}")
    print(f"{'='*60}")

    # Create folders
    subreddit_folder = Path(base_folder) / subreddit_name
    subreddit_folder.mkdir(parents=True, exist_ok=True)

    temp_folder = Path(base_folder) / "_temp"
    temp_folder.mkdir(parents=True, exist_ok=True)

    # Fetch all posts
    posts = fetch_all_posts(subreddit_name, SORT_BY, TIME_FILTER, POSTS_TO_FETCH)
    print(f"Found {len(posts)} posts total")

    downloaded = 0
    filtered_out = 0

    for post in posts:
        result = process_post(post, subreddit_folder, temp_folder)
        if result:
            downloaded += 1
        time.sleep(0.3)

    # Clean up temp folder
    try:
        temp_folder.rmdir()
    except:
        pass

    print(f"\nDownloaded {downloaded} Reels-compatible videos from r/{subreddit_name}")


def main():
    """Main entry point."""
    print("Reddit Video Scraper (Instagram Reels Filter)")
    print("="*60)

    if MIN_VIDEO_HEIGHT > 0:
        print(f"Minimum resolution: {MIN_VIDEO_HEIGHT}p")
    else:
        print("Resolution filtering: DISABLED")

    if FILTER_FOR_REELS:
        print(f"Filtering for aspect ratios: {MIN_ASPECT_RATIO:.2f} to {MAX_ASPECT_RATIO:.2f}")
    else:
        print("Aspect ratio filtering: DISABLED")

    if MAX_FILE_SIZE_MB > 0:
        print(f"Max file size: {MAX_FILE_SIZE_MB} MB")
        if COMPRESS_OVERSIZED:
            print(f"Compression: ENABLED (CRF {CRF_QUALITY}, two-pass fallback)")
        else:
            print("Compression: DISABLED (oversized files will be skipped)")
    else:
        print("File size limit: DISABLED")

    print(f"Posts to fetch per subreddit: {POSTS_TO_FETCH}")

    base_folder = Path(DOWNLOAD_FOLDER)
    base_folder.mkdir(parents=True, exist_ok=True)
    print(f"Download folder: {base_folder}")

    for i, subreddit_name in enumerate(SUBREDDITS):
        scrape_subreddit(subreddit_name, base_folder)

        if i < len(SUBREDDITS) - 1:
            print(f"\nWaiting {DELAY_BETWEEN_REQUESTS}s before next subreddit...")
            time.sleep(DELAY_BETWEEN_REQUESTS)

    print("\n" + "="*60)
    print("Scraping complete!")
    print(f"Files saved to: {base_folder}")


if __name__ == "__main__":
    main()
