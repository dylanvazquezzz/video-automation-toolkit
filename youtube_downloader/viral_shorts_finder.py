"""
Viral YouTube Shorts Finder & Downloader
Scans YouTube search results for shorts with 1M+ views and downloads them.
Downloads are sorted into subfolders by niche.

Usage:
    python viral_shorts_finder.py                          # Use default search terms
    python viral_shorts_finder.py --search "funny cats"    # Custom search terms
    python viral_shorts_finder.py --min-views 5000000      # 5M+ views only
    python viral_shorts_finder.py --scan-only              # Just show results, don't download
    python viral_shorts_finder.py --search-file terms.txt  # Load search terms from file
    python viral_shorts_finder.py --max-downloads 20       # Only download top 20
    python viral_shorts_finder.py --organize               # Sort existing downloads into niche folders
    python viral_shorts_finder.py --no-english-filter      # Include all languages (English filter ON by default)
"""

import subprocess
import sys
import time
import json
import io
import re
from pathlib import Path
import argparse
import concurrent.futures
import threading

# Fix Windows console encoding so emoji/unicode in titles don't crash
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Configuration ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
DOWNLOAD_FOLDER = SCRIPT_DIR / "viral_downloads"
ARCHIVE_FILE = SCRIPT_DIR / "viral_downloaded.txt"
FOUND_LOG = SCRIPT_DIR / "viral_found.json"

MIN_VIEWS = 1_000_000
MAX_DURATION = 60           # Shorts are <= 60 seconds
MIN_RESOLUTION = 1080
RESULTS_PER_SEARCH = 100    # How many results to scan per search term
SCAN_WORKERS = 4            # Parallel search queries
DOWNLOAD_WORKERS = 4        # Parallel video downloads
SCAN_TIMEOUT = 180          # Seconds per search query
DOWNLOAD_TIMEOUT = 120      # Seconds per video download
DEEP_LANG_CHECK_WORKERS = 8 # Parallel workers for deep language detection
DEEP_LANG_CHECK_TIMEOUT = 15 # Seconds per video language check

# Default search terms designed to surface viral shorts
DEFAULT_SEARCH_TERMS = [
    "viral shorts",
    "most viewed shorts",
    "trending shorts 2025",
    "shorts went viral",
    "viral short videos",
    "million views shorts",
    "popular shorts today",
    "best shorts this week",
    "funny viral shorts",
    "satisfying shorts viral",
    "amazing shorts millions views",
    "insane viral shorts",
    "shorts blowing up",
    "viral youtube shorts compilation",
    "shorts over 1 million views",
]


# ── Niche helpers ──────────────────────────────────────────────

def derive_niche(term: str) -> str:
    """Derive a clean niche folder name from a search term or hashtag."""
    clean = re.sub(r'#\w+', '', term).strip().lower()

    # Primary fillers: always stripped
    fillers = {
        'viral', 'shorts', 'videos', 'most', 'viewed', 'trending',
        'best', 'top', 'million', 'views', 'popular', 'today',
        'week', 'amazing', 'insane', 'over', 'went', 'short',
        'blowing', 'up', 'youtube', 'compilation',
        '2024', '2025', '2026',
    }
    words = [w for w in clean.split() if w not in fillers]

    if not words:
        return 'general'

    # Secondary fillers: stripped only if more meaningful words remain
    secondary = {
        'funny', 'oddly', 'clips', 'highlights', 'wins',
        'knockout', 'goals', 'hack', 'trick', 'lifestyle', 'wildlife',
    }
    core = [w for w in words if w not in secondary]
    if core:
        return core[0]
    return words[0]


# ── Scanning ───────────────────────────────────────────────────

def scan_search(query: str, niche: str, max_results: int, print_lock: threading.Lock,
                english_filter: bool = True) -> list[dict]:
    """Search YouTube for a query and return video metadata (fast, no downloads)."""
    search_url = f"ytsearch{max_results}:{query}"

    cmd = [
        sys.executable, "-m", "yt_dlp",
        search_url,
        "--flat-playlist",      # Fast: only grab metadata from search page
        "-j",                   # JSON output (one object per line)
        "--no-warnings",
        "--quiet",
    ]

    # Layer 1: Bias YouTube results toward English content
    if english_filter:
        cmd.extend(["--extractor-args", "youtube:lang=en"])
        cmd.extend(["--xff", "US"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SCAN_TIMEOUT)
    except subprocess.TimeoutExpired:
        with print_lock:
            print(f"  [TIMEOUT] Search: {query}")
        return []
    except Exception as e:
        with print_lock:
            print(f"  [ERROR]   Search: {query} - {e}")
        return []

    videos = []
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                vid = {
                    "id": data.get("id", ""),
                    "title": data.get("title", "Unknown"),
                    "views": data.get("view_count") or 0,
                    "duration": data.get("duration") or 0,
                    "channel": data.get("channel") or data.get("uploader") or "Unknown",
                    "niche": niche,
                }
                videos.append(vid)
            except (json.JSONDecodeError, KeyError):
                continue

    with print_lock:
        print(f"  Scanned: '{query}' [{niche}] -> {len(videos)} results")

    return videos


def scan_hashtag(tag: str, niche: str, print_lock: threading.Lock,
                 english_filter: bool = True) -> list[dict]:
    """Scan a YouTube hashtag page (these return only Shorts)."""
    url = f"https://www.youtube.com/hashtag/{tag}"

    cmd = [
        sys.executable, "-m", "yt_dlp",
        url,
        "--flat-playlist",
        "-j",
        "--no-warnings",
        "--quiet",
        "--playlist-end", "150",
    ]

    # Layer 1: Bias YouTube results toward English content
    if english_filter:
        cmd.extend(["--extractor-args", "youtube:lang=en"])
        cmd.extend(["--xff", "US"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SCAN_TIMEOUT)
    except subprocess.TimeoutExpired:
        with print_lock:
            print(f"  [TIMEOUT] Hashtag: #{tag}")
        return []
    except Exception as e:
        with print_lock:
            print(f"  [ERROR]   Hashtag: #{tag} - {e}")
        return []

    videos = []
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                vid = {
                    "id": data.get("id", ""),
                    "title": data.get("title", "Unknown"),
                    "views": data.get("view_count") or 0,
                    "duration": data.get("duration") or 0,
                    "channel": data.get("channel") or data.get("uploader") or "Unknown",
                    "niche": niche,
                }
                videos.append(vid)
            except (json.JSONDecodeError, KeyError):
                continue

    with print_lock:
        print(f"  Scanned: #{tag} [{niche}] -> {len(videos)} shorts")

    return videos


def scan_all(search_terms: list[str], max_results: int, workers: int,
             english_filter: bool = True) -> list[dict]:
    """Run all search queries + hashtag scans in parallel and return deduplicated results."""

    # Derive niche for each search term
    term_niches = {}
    for term in search_terms:
        niche = derive_niche(term)
        term_niches[term] = niche

    # Build hashtag list from search terms + bonus generic hashtags
    hashtag_niches = {}
    for term in search_terms:
        clean = term.lower().replace("#shorts", "").replace("#", "").strip()
        first_word = clean.split()[0] if clean.split() else ""
        if first_word and len(first_word) >= 3:
            hashtag_niches[first_word] = term_niches[term]

    # Generic bonus hashtags
    generic_bonus = {"viral", "shorts", "trending", "fyp", "funny", "satisfying"}
    for tag in generic_bonus:
        if tag not in hashtag_niches:
            hashtag_niches[tag] = tag if tag not in ("viral", "shorts", "trending", "fyp") else "general"

    total_searches = len(search_terms)
    total_hashtags = len(hashtag_niches)
    print(f"\nScanning {total_searches} search terms x {max_results} results + "
          f"{total_hashtags} hashtag feeds\n")

    print_lock = threading.Lock()
    all_videos = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        # Submit search queries
        search_futures = {
            executor.submit(scan_search, term, term_niches[term], max_results, print_lock, english_filter): term
            for term in search_terms
        }
        # Submit hashtag scans
        hashtag_futures = {
            executor.submit(scan_hashtag, tag, hashtag_niches[tag], print_lock, english_filter): tag
            for tag in hashtag_niches
        }

        all_futures = {**search_futures, **hashtag_futures}
        for future in concurrent.futures.as_completed(all_futures):
            try:
                videos = future.result()
                all_videos.extend(videos)
            except Exception as e:
                label = all_futures[future]
                print(f"  [ERROR] {label}: {e}")

    # Deduplicate by video ID (first niche seen wins)
    seen = set()
    unique = []
    for v in all_videos:
        if v["id"] and v["id"] not in seen:
            seen.add(v["id"])
            unique.append(v)

    print(f"\nTotal videos scanned: {len(all_videos)}")
    print(f"Unique videos: {len(unique)}")

    # Show niche distribution
    niche_counts = {}
    for v in unique:
        niche_counts[v["niche"]] = niche_counts.get(v["niche"], 0) + 1
    print("Niche breakdown: " + ", ".join(f"{k}: {v}" for k, v in sorted(niche_counts.items())))

    return unique


# ── Filtering ──────────────────────────────────────────────────

def filter_viral(videos: list[dict], min_views: int, max_duration: int) -> list[dict]:
    """Filter videos to only keep viral shorts."""
    viral = []
    no_views_data = 0
    too_long = 0
    too_few_views = 0

    for v in videos:
        if v["views"] == 0 or v["views"] is None:
            no_views_data += 1
            continue
        if v["duration"] > max_duration:
            too_long += 1
            continue
        if v["views"] < min_views:
            too_few_views += 1
            continue
        viral.append(v)

    # Sort by views descending (most viral first)
    viral.sort(key=lambda x: x["views"], reverse=True)

    print(f"  Passed filter: {len(viral)}")
    print(f"  Rejected - too few views: {too_few_views}")
    print(f"  Rejected - too long (>{max_duration}s): {too_long}")
    if no_views_data > 0:
        print(f"  Skipped - no view data: {no_views_data}")

    return viral


# ── English-only filtering (3-layer) ──────────────────────────

# Layer 2a: Non-Latin script Unicode ranges
_NON_LATIN_RANGES = [
    (0x0900, 0x097F),   # Devanagari (Hindi, Marathi, Sanskrit)
    (0x0980, 0x09FF),   # Bengali
    (0x0A00, 0x0A7F),   # Gurmukhi (Punjabi)
    (0x0A80, 0x0AFF),   # Gujarati
    (0x0B00, 0x0B7F),   # Oriya
    (0x0B80, 0x0BFF),   # Tamil
    (0x0C00, 0x0C7F),   # Telugu
    (0x0C80, 0x0CFF),   # Kannada
    (0x0D00, 0x0D7F),   # Malayalam
    (0x0E00, 0x0E7F),   # Thai
    (0x0E80, 0x0EFF),   # Lao
    (0x1000, 0x109F),   # Myanmar
    (0x1100, 0x11FF),   # Hangul Jamo (Korean)
    (0x3040, 0x309F),   # Hiragana (Japanese)
    (0x30A0, 0x30FF),   # Katakana (Japanese)
    (0x3400, 0x4DBF),   # CJK Unified Ext A
    (0x4E00, 0x9FFF),   # CJK Unified (Chinese/Japanese/Korean)
    (0xAC00, 0xD7AF),   # Hangul Syllables (Korean)
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x0400, 0x04FF),   # Cyrillic
    (0x0500, 0x052F),   # Cyrillic Supplement
    (0x10A0, 0x10FF),   # Georgian
    (0x0530, 0x058F),   # Armenian
    (0x0590, 0x05FF),   # Hebrew
]

# Layer 2b: Word lists for romanized non-English detection
# "Strong" words - a single match flags the title
_STRONG_NON_ENGLISH_WORDS = {
    # Hindi/Urdu romanized
    "kya", "kaise", "kyun", "kyon", "bahut", "bohot", "accha", "achha",
    "sath", "saath", "wala", "wali", "wale", "dekho", "dekhe", "dekhiye",
    "suniye", "sunno", "samjho", "padhai", "padhe", "gya", "gaya", "gayi",
    "raha", "rahi", "rahe", "hota", "hoti", "hote", "karna", "karke",
    "karein", "hogaya", "hogya", "zaroor", "zaruri", "kuch", "kuchh",
    "sabse", "sirf", "bilkul", "lekin", "magar", "isliye", "kyunki",
    "dosto", "bhai", "behen", "yaar", "aapko", "tumhe", "hamara",
    "tumhara", "unka", "unki", "inka", "inki", "sabko", "kisne",
    "kisko", "jaldi", "abhi", "pehle", "pahle", "baad", "zyada",
    "thoda", "bohot", "itna", "utna", "kitna", "jitna",
    # Portuguese
    "muito", "porque", "voce", "tambem", "agora", "quando",
    "como", "isso", "esta", "aqui", "onde", "depois",
    "antes", "sempre", "nunca", "tudo", "nada", "outro",
    "outra", "melhor", "pior", "grande", "pequeno", "bonito",
    "obrigado", "obrigada", "parabens", "entao",
    # Spanish (distinct from English)
    "porque", "cuando", "donde", "siempre", "nunca", "tambien",
    "ahora", "despues", "antes", "mejor", "peor", "grande",
    "pequeno", "bonito", "gracias", "entonces", "todavia",
    "aunque", "mientras", "aqui", "puede", "quiero", "necesito",
    "tiene", "bueno", "buena", "malo", "mala",
    # Turkish
    "bir", "olan", "icin", "gibi", "daha", "sonra", "nasil",
    "burada", "sana", "bana", "onun", "bizim", "onlar",
    # Indonesian / Malay
    "dengan", "untuk", "dari", "yang", "tidak", "sudah", "belum",
    "sangat", "bagus", "bisa", "harus", "boleh", "saya", "kami",
    "mereka", "dimana", "kapan", "kenapa", "bagaimana",
}

# "Weak" words - need 2+ matches to flag (common short words that overlap with English)
_WEAK_NON_ENGLISH_WORDS = {
    # Hindi/Urdu short words
    "ke", "ka", "ki", "se", "ko", "ne", "pe", "hai", "ho", "hei",
    "ye", "wo", "jo", "na", "ab", "tu", "mai", "mein", "aur",
    "par", "bhi", "toh", "nhi", "nahi", "tha", "thi", "the",
    "hum", "yeh", "woh", "jab", "tab", "kab",
    # Portuguese/Spanish short
    "eu", "tu", "nos", "ele", "ela", "mas", "nem", "sim",
    "nao", "bem", "mal", "meu", "seu", "uma",
    # Turkish short
    "ve", "bu", "ne", "mi", "mu",
}


def is_non_latin_script(title: str) -> bool:
    """Layer 2a: Return True if >15% of alphabetic chars are non-Latin script."""
    alpha_count = 0
    non_latin_count = 0
    for ch in title:
        if ch.isalpha():
            alpha_count += 1
            cp = ord(ch)
            for start, end in _NON_LATIN_RANGES:
                if start <= cp <= end:
                    non_latin_count += 1
                    break
    if alpha_count == 0:
        return False
    return (non_latin_count / alpha_count) > 0.15


def is_romanized_non_english(title: str) -> bool:
    """Layer 2b: Return True if title contains romanized non-English words."""
    # Normalize: lowercase, keep only letters and spaces
    cleaned = re.sub(r'[^a-zA-Z\s]', ' ', title.lower())
    words = set(cleaned.split())

    # Check strong words (1 match = flagged)
    if words & _STRONG_NON_ENGLISH_WORDS:
        return True

    # Check weak words (2+ matches = flagged)
    weak_hits = words & _WEAK_NON_ENGLISH_WORDS
    if len(weak_hits) >= 2:
        return True

    return False


def filter_non_english_titles(videos: list[dict]) -> tuple[list[dict], int]:
    """Layer 2: Filter out videos with non-English titles. Returns (passed, rejected_count)."""
    passed = []
    rejected = 0
    for v in videos:
        title = v.get("title", "")
        if is_non_latin_script(title) or is_romanized_non_english(title):
            rejected += 1
        else:
            passed.append(v)
    return passed, rejected


def check_video_language(video_id: str) -> str | None:
    """Layer 3: Use yt-dlp Python API to detect video language. Returns lang code or None."""
    try:
        import yt_dlp
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": DEEP_LANG_CHECK_TIMEOUT,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None

            # Check explicit language field
            lang = info.get("language")
            if lang:
                return lang

            # Check automatic_captions origin language
            auto_caps = info.get("automatic_captions") or {}
            if auto_caps:
                # The key for the source/origin language is typically listed first
                # or identified by "orig" suffix. Check common patterns.
                for key in auto_caps:
                    if key.startswith("en"):
                        return "en"
                # If no English auto-caption, return first key as detected language
                first_key = next(iter(auto_caps), None)
                if first_key:
                    return first_key

            return None
    except Exception:
        return None


def deep_language_filter(videos: list[dict], workers: int = DEEP_LANG_CHECK_WORKERS) -> tuple[list[dict], int]:
    """Layer 3: Deep language check using yt-dlp. Returns (passed, rejected_count)."""
    if not videos:
        return videos, 0

    print(f"  Deep language check on {len(videos)} candidates ({workers} workers)...")

    passed = []
    rejected = 0
    done = 0
    lock = threading.Lock()

    def check_one(video):
        lang = check_video_language(video["id"])
        return video, lang

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(check_one, v): v for v in videos}
        for future in concurrent.futures.as_completed(futures):
            done += 1
            try:
                video, lang = future.result()
                # Keep if: English, no language detected (ASMR/music/visual), or unknown
                if lang is None or lang.startswith("en"):
                    passed.append(video)
                else:
                    with lock:
                        rejected += 1
            except Exception:
                # On error, keep the video (benefit of the doubt)
                passed.append(futures[future])

            if done % 20 == 0 or done == len(videos):
                print(f"    Language checked: {done}/{len(videos)}...", end="\r")

    print(f"    Language checked: {done}/{len(videos)} - "
          f"passed: {len(passed)}, rejected: {rejected}      ")
    return passed, rejected


# ── Deep metadata scan (optional, slower but accurate) ─────────

def get_video_metadata(video_id: str) -> dict | None:
    """Extract full metadata for a single video (slower but guaranteed accurate)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        url,
        "--skip-download",
        "-j",
        "--no-warnings",
        "--quiet",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            return {
                "id": data.get("id", video_id),
                "title": data.get("title", "Unknown"),
                "views": data.get("view_count") or 0,
                "duration": data.get("duration") or 0,
                "channel": data.get("channel") or data.get("uploader") or "Unknown",
            }
    except Exception:
        pass
    return None


def deep_scan_videos(video_ids: list[str], workers: int = 8) -> list[dict]:
    """Get accurate metadata for a batch of videos (used when flat scan has no view data)."""
    print(f"\n  Deep-scanning {len(video_ids)} videos for accurate view counts...")

    results = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(get_video_metadata, vid): vid for vid in video_ids}
        for future in concurrent.futures.as_completed(futures):
            done += 1
            try:
                meta = future.result()
                if meta:
                    results.append(meta)
            except Exception:
                pass
            if done % 20 == 0 or done == len(video_ids):
                print(f"    {done}/{len(video_ids)} checked...", end="\r")

    print(f"    Resolved: {len(results)}/{len(video_ids)}              ")
    return results


# ── Downloading ────────────────────────────────────────────────

def read_archive() -> set[str]:
    """Read already-downloaded video IDs from the archive file."""
    if not ARCHIVE_FILE.exists():
        return set()
    with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
        ids = set()
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                ids.add(parts[1])
        return ids


def download_video(video: dict, download_folder: Path, min_height: int,
                   archive_lock: threading.Lock, print_lock: threading.Lock) -> str:
    """Download a single video into its niche subfolder. Returns 'downloaded', 'skipped', or 'failed'."""
    niche = video.get("niche", "general")
    niche_folder = download_folder / niche
    niche_folder.mkdir(parents=True, exist_ok=True)

    url = f"https://www.youtube.com/shorts/{video['id']}"
    # Force H.264 (avc1) so videos play in Windows default player
    # AV1/VP9 won't play without extra codecs installed
    format_string = (
        f"bestvideo[height>={min_height}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height>={min_height}][vcodec^=avc1]+bestaudio/"
        f"best[height>={min_height}][vcodec^=avc1]"
    )

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", format_string,
        "--merge-output-format", "mp4",
        "-o", str(niche_folder / "%(channel)s - %(title)s [%(id)s].%(ext)s"),
        "--no-playlist",
        "--no-warnings",
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT)
        combined = (result.stdout or "") + (result.stderr or "")

        if result.returncode == 0:
            with archive_lock:
                with open(ARCHIVE_FILE, "a", encoding="utf-8") as f:
                    f.write(f"youtube {video['id']}\n")
            return "downloaded"
        elif ("Requested format is not available" in combined
              or "no suitable formats" in combined.lower()):
            return "skipped"
        else:
            with print_lock:
                err = (result.stderr or "").strip()[:150]
                print(f"    ERROR [{video['id']}]: {err}")
            return "failed"

    except subprocess.TimeoutExpired:
        return "failed"
    except Exception as e:
        with print_lock:
            print(f"    EXCEPTION [{video['id']}]: {e}")
        return "failed"


def download_all(videos: list[dict], download_folder: Path, min_height: int, workers: int):
    """Download all matching videos in parallel, sorted into niche subfolders."""
    archived = read_archive()
    to_download = [v for v in videos if v["id"] not in archived]
    already_have = len(videos) - len(to_download)

    if already_have > 0:
        print(f"  Already downloaded: {already_have}")

    if not to_download:
        print("  Nothing new to download!")
        return

    # Show niche breakdown for this download batch
    niche_counts = {}
    for v in to_download:
        n = v.get("niche", "general")
        niche_counts[n] = niche_counts.get(n, 0) + 1
    print(f"  Downloading {len(to_download)} videos into {len(niche_counts)} niche folders:")
    for niche, count in sorted(niche_counts.items(), key=lambda x: -x[1]):
        print(f"    {niche}/  ({count} videos)")
    print()

    archive_lock = threading.Lock()
    print_lock = threading.Lock()

    downloaded = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    download_video, v, download_folder, min_height, archive_lock, print_lock
                ): v
                for v in to_download
            }

            for future in concurrent.futures.as_completed(futures):
                video = futures[future]
                try:
                    status = future.result()
                except Exception:
                    status = "failed"

                if status == "downloaded":
                    downloaded += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1

                total = downloaded + skipped + failed
                with print_lock:
                    print(f"  Progress: {total}/{len(to_download)} | "
                          f"Downloaded: {downloaded} | Skipped: {skipped} | "
                          f"Failed: {failed}    ", end="\r")

    except KeyboardInterrupt:
        print("\n\n  Ctrl+C received - stopping downloads...")

    elapsed = time.time() - start_time
    print(f"\n\n  Download complete in {elapsed:.0f}s")
    print(f"  Downloaded: {downloaded} | Skipped: {skipped} | Failed: {failed}")


# ── Organize existing downloads ───────────────────────────────

def organize_downloads(download_folder: Path, found_log: Path):
    """Move already-downloaded flat files into niche subfolders using viral_found.json."""
    if not found_log.exists():
        print(f"Error: {found_log} not found.")
        print("Run a scan first so the script knows which niche each video belongs to.")
        return

    with open(found_log, "r", encoding="utf-8") as f:
        found_videos = json.load(f)

    # Build ID -> niche map
    id_to_niche = {}
    for v in found_videos:
        vid_id = v.get("id", "")
        niche = v.get("niche", "general")
        if vid_id:
            id_to_niche[vid_id] = niche

    if not id_to_niche:
        print("No niche data found. Run a scan with the latest script first.")
        return

    # Find files in the root of download_folder (not already in subfolders)
    moved = 0
    skipped = 0
    no_match = 0

    for file in download_folder.iterdir():
        if not file.is_file() or not file.suffix == ".mp4":
            continue

        # Extract video ID from filename pattern: ... [VIDEO_ID].mp4
        match = re.search(r'\[([a-zA-Z0-9_-]{11})\]\.mp4$', file.name)
        if not match:
            no_match += 1
            continue

        vid_id = match.group(1)
        niche = id_to_niche.get(vid_id, "general")
        niche_folder = download_folder / niche
        dest = niche_folder / file.name

        if dest.exists():
            skipped += 1
            continue

        niche_folder.mkdir(exist_ok=True)
        file.rename(dest)
        moved += 1

    print(f"\n  Organize complete:")
    print(f"    Moved into subfolders: {moved}")
    print(f"    Already in place: {skipped}")
    if no_match > 0:
        print(f"    Could not match ID: {no_match}")

    # Show folder structure
    print(f"\n  Folder structure:")
    for folder in sorted(download_folder.iterdir()):
        if folder.is_dir():
            count = sum(1 for f in folder.iterdir() if f.is_file())
            print(f"    {folder.name}/  ({count} videos)")


# ── Formatting helpers ─────────────────────────────────────────

def format_views(n: int) -> str:
    """Format view count as human-readable string."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


# ── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Find and download viral YouTube Shorts (1M+ views)"
    )
    parser.add_argument(
        "--search", nargs="+",
        help="Custom search terms (replaces defaults)"
    )
    parser.add_argument(
        "--search-file", type=str,
        help="File with search terms, one per line"
    )
    parser.add_argument(
        "--min-views", type=int, default=MIN_VIEWS,
        help=f"Minimum view count (default: {MIN_VIEWS:,})"
    )
    parser.add_argument(
        "--results-per-search", type=int, default=RESULTS_PER_SEARCH,
        help=f"Results to scan per search term (default: {RESULTS_PER_SEARCH})"
    )
    parser.add_argument(
        "--min-resolution", type=int, default=MIN_RESOLUTION,
        help=f"Minimum video height in pixels (default: {MIN_RESOLUTION})"
    )
    parser.add_argument(
        "--scan-workers", type=int, default=SCAN_WORKERS,
        help=f"Parallel search queries (default: {SCAN_WORKERS})"
    )
    parser.add_argument(
        "--download-workers", type=int, default=DOWNLOAD_WORKERS,
        help=f"Parallel downloads (default: {DOWNLOAD_WORKERS})"
    )
    parser.add_argument(
        "--scan-only", action="store_true",
        help="Only scan and show results, don't download"
    )
    parser.add_argument(
        "--deep-scan", action="store_true",
        help="Re-verify view counts with full metadata extraction (slower but more accurate)"
    )
    parser.add_argument(
        "--max-downloads", type=int, default=0,
        help="Limit how many videos to download, 0 = no limit (default: 0)"
    )
    parser.add_argument(
        "--organize", action="store_true",
        help="Sort existing flat downloads into niche subfolders (uses viral_found.json)"
    )
    parser.add_argument(
        "--no-english-filter", action="store_true",
        help="Disable English-only filtering (by default, non-English videos are filtered out)"
    )
    args = parser.parse_args()

    # ── Organize mode ─────────────────────────────────────────
    if args.organize:
        print("=" * 60)
        print("  Organizing existing downloads into niche folders...")
        print("=" * 60)

        # If viral_found.json has no niche data, re-scan first
        needs_rescan = False
        if FOUND_LOG.exists():
            with open(FOUND_LOG, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data and "niche" not in data[0]:
                needs_rescan = True
        else:
            needs_rescan = True

        if needs_rescan:
            print("\n  viral_found.json needs niche tags. Running a quick scan first...\n")
            # Determine search terms for the rescan
            if args.search:
                search_terms = args.search
            elif args.search_file:
                path = Path(args.search_file)
                if not path.exists():
                    print(f"Error: {path} not found")
                    return
                with open(path, "r", encoding="utf-8") as f:
                    search_terms = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            else:
                search_terms = DEFAULT_SEARCH_TERMS

            all_videos = scan_all(search_terms, args.results_per_search, args.scan_workers)
            viral = filter_viral(all_videos, args.min_views, MAX_DURATION)
            if viral:
                with open(FOUND_LOG, "w", encoding="utf-8") as f:
                    json.dump(viral, f, indent=2, ensure_ascii=False)
                print(f"  Updated {FOUND_LOG} with niche tags.")

        organize_downloads(DOWNLOAD_FOLDER, FOUND_LOG)

        print(f"\n{'=' * 60}")
        print(f"  Done!")
        print(f"{'=' * 60}")
        return

    # ── Normal mode ───────────────────────────────────────────

    # Determine search terms
    if args.search:
        search_terms = args.search
    elif args.search_file:
        path = Path(args.search_file)
        if not path.exists():
            print(f"Error: {path} not found")
            return
        with open(path, "r", encoding="utf-8") as f:
            search_terms = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    else:
        search_terms = DEFAULT_SEARCH_TERMS

    english_filter = not args.no_english_filter

    print("=" * 60)
    print("  Viral YouTube Shorts Finder")
    print(f"  Min views:   {args.min_views:,}")
    print(f"  Search terms: {len(search_terms)}")
    print(f"  Results/term: {args.results_per_search}")
    print(f"  Resolution:   {args.min_resolution}p+")
    print(f"  English only: {'YES' if english_filter else 'OFF'}")
    print("=" * 60)

    # ── Phase 1: Scan ─────────────────────────────────────────
    all_videos = scan_all(search_terms, args.results_per_search, args.scan_workers, english_filter)

    if not all_videos:
        print("\nNo videos found. Try different search terms.")
        return

    # ── Phase 2: Filter ───────────────────────────────────────
    print(f"\nFiltering for shorts (<={MAX_DURATION}s) with {args.min_views:,}+ views...")
    viral = filter_viral(all_videos, args.min_views, MAX_DURATION)

    # ── Phase 2.5: Optional deep scan ─────────────────────────
    if args.deep_scan:
        no_data = [v for v in all_videos
                   if (v["views"] == 0 or v["views"] is None)
                   and 0 < v["duration"] <= MAX_DURATION]
        if no_data:
            print(f"\n  {len(no_data)} videos had no view data, deep-scanning up to 200...")
            deep_results = deep_scan_videos([v["id"] for v in no_data[:200]], workers=8)
            extra_viral = [v for v in deep_results if v["views"] >= args.min_views]
            if extra_viral:
                # Inherit niche from the original video entry
                no_data_map = {v["id"]: v.get("niche", "general") for v in no_data}
                for v in extra_viral:
                    v["niche"] = no_data_map.get(v["id"], "general")
                print(f"  Found {len(extra_viral)} additional viral shorts from deep scan!")
                viral.extend(extra_viral)
                seen = set()
                deduped = []
                for v in viral:
                    if v["id"] not in seen:
                        seen.add(v["id"])
                        deduped.append(v)
                viral = sorted(deduped, key=lambda x: x["views"], reverse=True)

    # ── Phase 2.7: English-only filtering ────────────────────
    if english_filter and viral:
        pre_count = len(viral)
        lang_rejected = 0

        # Split: already-downloaded videos skip the expensive deep language check
        archived = read_archive()
        already_have = [v for v in viral if v["id"] in archived]
        new_candidates = [v for v in viral if v["id"] not in archived]

        if already_have:
            print(f"\n  Skipping deep language check for {len(already_have)} already-downloaded videos")

        # Layer 2: Title-based filtering (instant - run on new candidates only)
        title_rejected = 0
        if new_candidates:
            print(f"  English filter - title check ({len(new_candidates)} new videos)...")
            new_candidates, title_rejected = filter_non_english_titles(new_candidates)
            if title_rejected:
                print(f"    Rejected by title: {title_rejected}")

        # Layer 3: Deep language check (slower - run on new candidates only)
        if new_candidates:
            print(f"  English filter - deep language check ({len(new_candidates)} new videos)...")
            lang_start = time.time()
            new_candidates, lang_rejected = deep_language_filter(new_candidates, DEEP_LANG_CHECK_WORKERS)
            lang_elapsed = time.time() - lang_start
            if lang_rejected:
                print(f"    Rejected by language: {lang_rejected}")
            print(f"    Deep check took {lang_elapsed:.0f}s")

        # Rejoin: already-downloaded + filtered new candidates, re-sort by views
        viral = already_have + new_candidates
        viral.sort(key=lambda x: x["views"], reverse=True)

        total_rejected = title_rejected + lang_rejected
        print(f"  English filter total: {pre_count} -> {len(viral)} "
              f"({total_rejected} non-English removed, {len(already_have)} already downloaded skipped)")

    if not viral:
        print("\nNo viral shorts found matching your criteria.")
        print("Try: --min-views 500000  or different search terms.")
        return

    # ── Show results ──────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  Found {len(viral)} viral shorts!")
    print(f"{'=' * 60}\n")

    show_count = min(len(viral), 50)
    for i, v in enumerate(viral[:show_count], 1):
        views_str = format_views(v["views"])
        title = v["title"][:50]
        niche = v.get("niche", "general")
        print(f"  {i:3}. [{views_str:>6}] [{niche:>12}] {title}")
        print(f"       Channel: {v['channel']}  |  {v['duration']}s")

    if len(viral) > show_count:
        print(f"\n  ... and {len(viral) - show_count} more")

    # Show niche summary
    niche_counts = {}
    for v in viral:
        n = v.get("niche", "general")
        niche_counts[n] = niche_counts.get(n, 0) + 1
    print(f"\n  Niche summary:")
    for niche, count in sorted(niche_counts.items(), key=lambda x: -x[1]):
        print(f"    {niche}/  -> {count} viral shorts")

    # Save full list as JSON
    with open(FOUND_LOG, "w", encoding="utf-8") as f:
        json.dump(viral, f, indent=2, ensure_ascii=False)
    print(f"\n  Full list saved to: {FOUND_LOG}")

    if args.scan_only:
        print("\n  --scan-only mode: skipping downloads.")
        return

    # ── Phase 3: Download ─────────────────────────────────────
    to_dl = viral
    if args.max_downloads > 0:
        to_dl = viral[:args.max_downloads]
        print(f"\n  Limiting to top {args.max_downloads} by views")

    DOWNLOAD_FOLDER.mkdir(exist_ok=True)
    print(f"  Downloading to: {DOWNLOAD_FOLDER}\n")
    download_all(to_dl, DOWNLOAD_FOLDER, args.min_resolution, args.download_workers)

    print(f"\n{'=' * 60}")
    print(f"  Done! Videos saved to: {DOWNLOAD_FOLDER}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
