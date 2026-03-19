# Video Automation Toolkit

A collection of Python scripts for automating video downloading, processing, and distribution workflows. Built around `yt-dlp` and `ffmpeg`.

## Tools

| Script | What it does |
|--------|-------------|
| [`youtube_downloader/download_shorts.py`](#download-youtube-shorts) | Download YouTube Shorts by URL or entire channel |
| [`youtube_downloader/viral_shorts_finder.py`](#viral-shorts-finder) | Find and download viral Shorts (1M+ views) sorted by niche |
| [`youtube_downloader/convert_to_h264.py`](#convert-to-h264) | Batch convert AV1/VP9 videos to H.264 for universal playback |
| [`reddit_scraper/reddit_scraper.py`](#reddit-video-scraper) | Scrape subreddit videos filtered for Instagram Reels compatibility |
| [`video_processing/watermark_videos.py`](#video-watermarker) | Add an image watermark overlay to a batch of videos |
| [`video_processing/merge_videos.py`](#video-merger) | Merge main video + outro + replace audio, GPU/CPU encoding |
| [`upload/upload_to_sheet_oauth.py`](#upload-to-google-sheets) | Upload media to catbox.moe and log direct URLs to Google Sheets |

---

## Requirements

**Python 3.10+**

```bash
pip install -r requirements.txt
```

**ffmpeg** must also be installed and available in your PATH:
- macOS: `brew install ffmpeg`
- Windows: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add `bin/` to PATH
- Ubuntu/Debian: `sudo apt install ffmpeg`

---

## Download YouTube Shorts

**`youtube_downloader/download_shorts.py`**

Downloads YouTube Shorts at 1080p+ quality. Supports individual video URLs, channel URLs, and maintains a download archive to avoid re-downloading.

```bash
# Download from a links.txt file (one URL per line)
python download_shorts.py

# Download all Shorts from a channel
python download_shorts.py --channel https://www.youtube.com/@channelname

# Custom options
python download_shorts.py --channel https://www.youtube.com/@channelname \
  --min-resolution 720 \
  --workers 8 \
  --max-failures 10
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--channel` | — | Channel URL to download all Shorts from |
| `--min-resolution` | `1080` | Minimum video height in pixels |
| `--workers` | `4` | Number of parallel downloads |
| `--max-failures` | `20` | Abort after this many consecutive failures |

**Output:** `downloads/` folder in the script directory. Archive tracked in `downloaded.txt`.

---

## Viral Shorts Finder

**`youtube_downloader/viral_shorts_finder.py`**

Scans YouTube search results and hashtag feeds to find Shorts with 1M+ views, filters for English content, and downloads them sorted into niche subfolders.

```bash
# Use default search terms (general viral/trending categories)
python viral_shorts_finder.py

# Custom search terms
python viral_shorts_finder.py --search "cooking shorts" "food hacks"

# Load search terms from a file
python viral_shorts_finder.py --search-file my_terms.txt

# Scan only, no download
python viral_shorts_finder.py --scan-only

# Higher view threshold
python viral_shorts_finder.py --min-views 5000000

# Limit downloads to top 20
python viral_shorts_finder.py --max-downloads 20

# Disable English filter
python viral_shorts_finder.py --no-english-filter

# Re-sort already-downloaded files into niche subfolders
python viral_shorts_finder.py --organize
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--search` | built-in list | Custom search terms |
| `--search-file` | — | File with search terms (one per line) |
| `--min-views` | `1,000,000` | Minimum view count |
| `--results-per-search` | `100` | Results scanned per search term |
| `--min-resolution` | `1080` | Minimum video height in pixels |
| `--scan-workers` | `4` | Parallel search queries |
| `--download-workers` | `4` | Parallel downloads |
| `--scan-only` | off | Show results without downloading |
| `--deep-scan` | off | Re-verify view counts with full metadata (slower) |
| `--max-downloads` | `0` (no limit) | Cap number of videos to download |
| `--organize` | off | Sort existing downloads into niche folders |
| `--no-english-filter` | off | Disable English-only filtering |

**Output:** `viral_downloads/<niche>/` — videos organized by detected niche. Scan results saved to `viral_found.json`.

---

## Convert to H.264

**`youtube_downloader/convert_to_h264.py`**

Batch converts AV1, VP9, HEVC, and VP8 videos to H.264 in-place so they play in Windows Media Player and other software without additional codecs. Skips files already in H.264.

```bash
python convert_to_h264.py
```

Processes all `.mp4` files in the `viral_downloads/` folder (relative to the script). Uses 4 parallel workers by default — edit `WORKERS` at the top of the script to change.

---

## Reddit Video Scraper

**`reddit_scraper/reddit_scraper.py`**

Scrapes videos from subreddits using Reddit's public JSON API (no API key required). Filters by aspect ratio and resolution for Instagram Reels compatibility. Automatically compresses oversized files using ffmpeg.

**Setup:** Edit the configuration block at the top of the script:

```python
SUBREDDITS = ["MemeVideos", "funny", "PublicFreakout"]
DOWNLOAD_FOLDER = "/path/to/your/download/folder"
POSTS_TO_FETCH = 500
SORT_BY = "top"         # hot | new | top | rising
TIME_FILTER = "all"     # hour | day | week | month | year | all
FILTER_FOR_REELS = True
MIN_VIDEO_HEIGHT = 720
MAX_FILE_SIZE_MB = 300
```

```bash
python reddit_scraper.py
```

**Features:**
- Paginates automatically up to `POSTS_TO_FETCH` posts
- Downloads Reddit-hosted videos with audio (merges separate streams via ffmpeg)
- Filters by aspect ratio (configurable min/max) for Reels compatibility
- CRF compression + two-pass fallback for oversized files
- Rate-limited to avoid hitting Reddit's API limits

---

## Video Watermarker

**`video_processing/watermark_videos.py`**

Adds an image overlay to the bottom-center of every video in a folder using ffmpeg.

**Folder setup:**
```
watermark_videos.py
main_videos/       <- put your input videos here
watermarks/        <- put your watermark image here (JPG/PNG/WebP)
finished/          <- output videos are saved here
```

**Configuration:** Edit `FFMPEG_PATH` at the top of the script to point to your ffmpeg installation (Windows path is set by default — update for macOS/Linux to just `"ffmpeg"` if it's in your PATH).

```bash
python watermark_videos.py
```

The watermark is scaled to 30% of the video width and positioned near the bottom-center. Edit the `filter_complex` in `add_watermark()` to adjust size or position.

---

## Video Merger

**`video_processing/merge_videos.py`**

Merges a main video with a randomly selected outro, replaces the audio track with a randomly selected MP3, scales everything to 1080x1920 (9:16 vertical) at 30fps, and encodes with NVENC (GPU) if available or falls back to libx264 (CPU).

**Folder setup:**
```
merge_videos.py
main_videos/       <- input videos (.mp4)
outros/            <- outro clips (.mp4 / .MP4)
audios/            <- background music (.mp3)
finished/          <- output videos saved here
```

```bash
# Process all videos
python merge_videos.py

# Test with one video first
python merge_videos.py --test
```

**Configuration** (edit at top of script):
```python
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30
USE_NVENC = True      # Set False to force CPU encoding
NVENC_CQ = 20         # Quality (lower = better)
X264_CRF = 18         # Quality for CPU fallback
WORKERS = 4           # Parallel encodes
```

---

## Upload to Google Sheets

**`upload/upload_to_sheet_oauth.py`**

Uploads media files (MP4, WebP, JPG, PNG, MP3) to [catbox.moe](https://catbox.moe) (permanent free hosting) and writes the direct URLs into a Google Sheet. Falls back to litterbox.catbox.moe (72h retention) if catbox fails.

**First-time setup:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project
2. Enable the **Google Sheets API**
3. Create OAuth 2.0 credentials (Desktop app), download as `credentials.json`
4. Place `credentials.json` in the same folder as the script
5. On first run, a browser window will open for you to authorize access

```bash
# Upload a single file
python upload_to_sheet_oauth.py video.mp4 <spreadsheet_id>

# Upload an entire folder
python upload_to_sheet_oauth.py --folder /path/to/folder <spreadsheet_id>

# Specify sheet tab and limit uploads
python upload_to_sheet_oauth.py --folder /path/to/folder <spreadsheet_id> \
  --sheet-name MyTab \
  --limit 50 \
  --workers 8
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--folder` | — | Upload all media files in a folder |
| `--sheet-name` | `Sheet1` | Google Sheet tab name |
| `--row` | append | Specific row to write to (single file mode only) |
| `--limit` | no limit | Max number of files to upload |
| `--workers` | `4` | Parallel upload threads |

The spreadsheet ID is the long string in your Google Sheet's URL:
`https://docs.google.com/spreadsheets/d/`**`<spreadsheet_id>`**`/edit`

Credentials are cached in `token.pickle` after the first login.

---

## License

MIT
