#!/usr/bin/env python3
"""
Script to upload media files (MP4, WebP, JPG, PNG) to catbox.moe and add the link(s) to Google Sheets
Using OAuth 2.0 authentication (user login)
Falls back to litterbox.catbox.moe (72h retention) if catbox fails

catbox.moe provides permanent hosting with direct URLs that work with Instagram API
"""
import os
import sys
import glob
import time
import pickle
import re
import hashlib
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Shared session with proper User-Agent to avoid being blocked
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
})

# Fix Windows console encoding for Unicode characters (emojis, etc.)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Thread-safe print
_print_lock = threading.Lock()
def tprint(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)
        sys.stdout.flush()

# Regex to detect emojis and other non-ASCII symbols in filenames
_EMOJI_PATTERN = re.compile(
    "[\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"   # symbols & pictographs
    "\U0001F680-\U0001F6FF"   # transport & map
    "\U0001F1E0-\U0001F1FF"   # flags
    "\U00002700-\U000027BF"   # dingbats
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0001F900-\U0001F9FF"   # supplemental symbols
    "\U0001FA00-\U0001FA6F"   # chess symbols
    "\U0001FA70-\U0001FAFF"   # symbols extended-A
    "\U00002600-\U000026FF"   # misc symbols
    "\U0000200D"              # zero width joiner
    "\U00002B50"              # star
    "\U0000231A-\U0000231B"   # watch/hourglass
    "\U00002934-\U00002935"   # arrows
    "\U000025AA-\U000025AB"   # squares
    "\U000025FB-\U000025FE"   # squares
    "\U00002B05-\U00002B07"   # arrows
    "\U00002B1B-\U00002B1C"   # squares
    "]+", flags=re.UNICODE
)

def check_filenames_for_emojis(file_list):
    """Check if any filenames contain emojis. Returns list of bad filenames."""
    bad_files = []
    for f in file_list:
        name = os.path.basename(f)
        if _EMOJI_PATTERN.search(name):
            bad_files.append(name)
    return bad_files

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Google Sheets API setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def get_credentials():
    """
    Get Google OAuth credentials, prompting user to login if needed

    Returns:
        Credentials object
    """
    creds = None
    # Resolve paths relative to the script's directory, not the working directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    token_file = os.path.join(script_dir, 'token.pickle')
    credentials_file = os.path.join(script_dir, 'credentials.json')

    # Check if we have saved credentials
    if os.path.exists(token_file):
        with open(token_file, 'rb') as token:
            creds = pickle.load(token)

    # If no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_file):
                print("Error: credentials.json file not found!")
                print(f"Expected at: {credentials_file}")
                print("Please follow the setup instructions to download your OAuth credentials.")
                sys.exit(1)

            print("First time setup - you'll need to log in with your Google account...")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for next time
        with open(token_file, 'wb') as token:
            pickle.dump(creds, token)
        print("[OK] Credentials saved for future use")

    return creds

def upload_to_gofile(file_path, account_token):
    """
    Upload file to gofile.io using account credentials

    Args:
        file_path: Path to the media file
        account_token: GoFile account token

    Returns:
        str: Download URL from gofile.io
    """
    print(f"Uploading {os.path.basename(file_path)} to gofile.io...")

    # Step 1: Get the best server
    try:
        server_response = requests.get('https://api.gofile.io/servers', timeout=30)
        server_response.raise_for_status()
        server_data = server_response.json()

        if server_data.get('status') != 'ok':
            raise Exception(f"Failed to get server: {server_data}")

        server = server_data['data']['servers'][0]['name']
        print(f"  Using server: {server}")
    except Exception as e:
        raise Exception(f"Failed to get gofile.io server: {e}")

    # Step 2: Upload the file
    try:
        upload_url = f'https://{server}.gofile.io/uploadFile'

        with open(file_path, 'rb') as file:
            files = {'file': file}
            data = {
                'token': account_token
            }

            response = requests.post(upload_url, files=files, data=data, timeout=600)
            response.raise_for_status()
            result = response.json()

        if result.get('status') != 'ok':
            raise Exception(f"Upload failed: {result}")

        download_url = result['data']['downloadPage']
        print(f"[OK] Upload successful: {download_url}")
        return download_url

    except Exception as e:
        raise Exception(f"Failed to upload to gofile.io: {e}")


def safe_filename(file_path):
    """Create an ASCII-safe filename for upload, preserving the extension."""
    name = os.path.basename(file_path)
    ext = os.path.splitext(name)[1]
    # Strip to ASCII-safe chars only
    clean = re.sub(r'[^\w\s\-.]', '', name.encode('ascii', 'ignore').decode())
    clean = clean.strip()[:80]
    if not clean or clean == ext:
        clean = hashlib.md5(name.encode()).hexdigest()[:12]
    if not clean.endswith(ext):
        clean = os.path.splitext(clean)[0] + ext
    return clean


def upload_to_catbox(file_path, max_retries=2):
    """
    Upload file to catbox.moe (permanent hosting, direct URLs) with retry logic
    """
    url = "https://catbox.moe/user/api.php"
    last_error = None
    fname = os.path.basename(file_path)

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                tprint(f"  [{fname}] RETRY attempt {attempt}/{max_retries}...")
            else:
                tprint(f"  [{fname}] Uploading to catbox.moe...")

            upload_name = safe_filename(file_path)
            with open(file_path, 'rb') as file:
                files = {'fileToUpload': (upload_name, file)}
                data = {'reqtype': 'fileupload'}
                response = SESSION.post(url, files=files, data=data, timeout=300)

            if response.status_code == 200:
                download_url = response.text.strip()
                if download_url.startswith('http'):
                    tprint(f"  [{fname}] OK: {download_url}")
                    return download_url
                else:
                    last_error = Exception(f"catbox response: {download_url}")
            else:
                last_error = Exception(f"catbox status {response.status_code}: {response.text[:200]}")
        except Exception as e:
            last_error = e

        if attempt < max_retries:
            tprint(f"  [{fname}] WARN: {last_error} - retrying in 5s...")
            time.sleep(5)

    raise last_error


def upload_to_litterbox(file_path, expiry='72h'):
    """
    Upload file to litterbox.catbox.moe (temporary hosting, 72h retention)
    """
    fname = os.path.basename(file_path)
    tprint(f"  [{fname}] Falling back to litterbox ({expiry})...")

    url = "https://litterbox.catbox.moe/resources/internals/api.php"
    upload_name = safe_filename(file_path)
    with open(file_path, 'rb') as file:
        files = {'fileToUpload': (upload_name, file)}
        data = {'reqtype': 'fileupload', 'time': expiry}
        response = SESSION.post(url, files=files, data=data, timeout=300)

    if response.status_code == 200:
        download_url = response.text.strip()
        if download_url.startswith('http'):
            tprint(f"  [{fname}] OK litterbox: {download_url}")
            return download_url
        else:
            raise Exception(f"litterbox response: {download_url}")
    else:
        raise Exception(f"litterbox status {response.status_code}: {response.text[:200]}")


def upload_file(file_path):
    """
    Upload file: tries catbox.moe (2 attempts), then falls back to litterbox (72h)
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    try:
        return upload_to_catbox(file_path)
    except Exception as e:
        tprint(f"  [{os.path.basename(file_path)}] catbox failed: {e}")
        return upload_to_litterbox(file_path)

def add_link_to_sheet(spreadsheet_id, link, creds, sheet_name='Sheet1', row=None, filename=None):
    """
    Add the link to a Google Sheet

    Args:
        spreadsheet_id: The ID of the Google Sheet
        link: The URL to add
        creds: Google OAuth credentials
        sheet_name: Name of the sheet tab (default: 'Sheet1')
        row: Specific row to update (if None, appends to next available row)
        filename: Optional filename to add in column A (link goes in column B)
    """
    print(f"Adding link to Google Sheet...")

    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    # Prepare the data to write
    if filename:
        values = [[filename, link]]
    else:
        values = [[link]]

    if row is not None:
        # Update specific row (must be row 2 or later)
        if row < 2:
            row = 2
        if filename:
            range_name = f'{sheet_name}!A{row}:B{row}'
        else:
            range_name = f'{sheet_name}!B{row}'
        body = {'values': values}
        result = sheet.values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption='RAW',
            body=body
        ).execute()
        print(f"[OK] Link added to row {row}")
    else:
        # Append to next available row starting from row 2
        if filename:
            range_name = f'{sheet_name}!A2:B'
        else:
            range_name = f'{sheet_name}!B2:B'
        body = {'values': values}
        result = sheet.values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption='RAW',
            body=body
        ).execute()
        print(f"[OK] Link appended to sheet")

    return result

def add_batch_links_to_sheet(spreadsheet_id, data_rows, creds, sheet_name='Sheet1'):
    """
    Add multiple links to a Google Sheet at once

    Args:
        spreadsheet_id: The ID of the Google Sheet
        data_rows: List of [filename, link] pairs
        creds: Google OAuth credentials
        sheet_name: Name of the sheet tab (default: 'Sheet1')
    """
    print(f"Adding {len(data_rows)} links to Google Sheet...")

    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    # Append all rows at once, starting from row 2 (after row 1)
    range_name = f'{sheet_name}!A2:B'
    body = {'values': data_rows}
    result = sheet.values().append(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='RAW',
        body=body
    ).execute()
    print(f"[OK] All {len(data_rows)} links added to sheet")

    return result

def process_folder(folder_path, spreadsheet_id, creds, sheet_name='Sheet1', limit=None, workers=4):
    """
    Process all media files in a folder and upload them in parallel.
    """
    # Supported file extensions
    extensions = ['*.mp4', '*.MP4', '*.webp', '*.WEBP', '*.jpg', '*.JPG', '*.jpeg', '*.JPEG', '*.png', '*.PNG', '*.mp3', '*.MP3']

    # Find all media files in the folder
    media_files = []
    for ext in extensions:
        pattern = os.path.join(folder_path, ext)
        media_files.extend(glob.glob(pattern))

    # Remove duplicates (Windows is case-insensitive)
    media_files = list(set(media_files))

    if not media_files:
        print(f"No media files found in {folder_path}")
        return

    # Check for emojis in filenames before processing
    bad_files = check_filenames_for_emojis(media_files)
    if bad_files:
        print(f"\n[ERROR] {len(bad_files)} file(s) have emojis in their names!")
        print("Emojis cause upload failures on catbox.moe. Please rename these files first:\n")
        for name in sorted(bad_files)[:20]:
            print(f"  - {name}")
        if len(bad_files) > 20:
            print(f"  ... and {len(bad_files) - 20} more")
        print(f"\nAborting. Rename the files and try again.")
        sys.exit(1)

    # Sort files alphabetically, but put all IMG* files at the end
    def sort_key(x):
        name = os.path.basename(x).upper()
        if name.startswith('IMG'):
            return (1, name)
        return (0, name)
    media_files.sort(key=sort_key)

    if limit and limit < len(media_files):
        print(f"Found {len(media_files)} media file(s), limiting to first {limit}")
        media_files = media_files[:limit]
    else:
        print(f"Found {len(media_files)} media file(s) to process")
    print(f"Uploading with {workers} parallel workers")
    print("-" * 50)

    # Upload files in parallel
    # results dict: index -> (filename, link) to preserve order for the sheet
    results = {}
    failed = 0
    total = len(media_files)

    def _upload_one(idx, fpath):
        fname = os.path.basename(fpath)
        tprint(f"\n[{idx}/{total}] Starting: {fname}")
        link = upload_file(fpath)
        return idx, fname, link

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, file_path in enumerate(media_files, 1):
            fut = pool.submit(_upload_one, i, file_path)
            futures[fut] = i

        for fut in as_completed(futures):
            try:
                idx, fname, link = fut.result()
                results[idx] = [fname, link]
            except Exception as e:
                idx = futures[fut]
                fname = os.path.basename(media_files[idx - 1])
                tprint(f"[FAIL] {fname}: {e}")
                failed += 1

    # Build data_rows in original sort order
    data_rows = [results[i] for i in sorted(results.keys())]
    successful = len(data_rows)

    # Add all successful uploads to the sheet at once
    if data_rows:
        print("\n" + "=" * 50)
        print(f"Uploading {len(data_rows)} link(s) to Google Sheet...")
        add_batch_links_to_sheet(spreadsheet_id, data_rows, creds, sheet_name)

    # Summary
    print("\n" + "=" * 50)
    print(f"[OK] Successfully processed: {successful}")
    if failed > 0:
        print(f"[FAIL] Failed: {failed}")
    print("=" * 50)

def main():
    """Main function"""
    if len(sys.argv) < 3:
        print("Usage:")
        print("  Single file: python upload_to_sheet_oauth.py <media_file> <spreadsheet_id>")
        print("  Folder mode: python upload_to_sheet_oauth.py --folder <folder_path> <spreadsheet_id>")
        print("\nSupported formats: MP4, WebP, JPG, JPEG, PNG")
        print("\nExamples:")
        print("  python upload_to_sheet_oauth.py video.mp4 1abc123xyz")
        print("  python upload_to_sheet_oauth.py image.webp 1abc123xyz")
        print('  python upload_to_sheet_oauth.py --folder "/path/to/your/folder" YOUR_SPREADSHEET_ID')
        print("\nOptional arguments:")
        print("  --sheet-name <name>  Sheet tab name (default: Sheet1)")
        print("  --row <number>       Specific row to update (single file mode only)")
        sys.exit(1)

    # Check if folder mode
    folder_mode = False
    if sys.argv[1] == '--folder':
        folder_mode = True
        if len(sys.argv) < 4:
            print("Error: --folder requires a folder path")
            sys.exit(1)
        folder_path = sys.argv[2]
        spreadsheet_id = sys.argv[3]
        arg_offset = 4
    else:
        media_file = sys.argv[1]
        spreadsheet_id = sys.argv[2]
        arg_offset = 3

    # Parse optional arguments
    sheet_name = 'Sheet1'
    row = None
    limit = None
    workers = 4

    i = arg_offset
    while i < len(sys.argv):
        if sys.argv[i] == '--sheet-name' and i + 1 < len(sys.argv):
            sheet_name = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--row' and i + 1 < len(sys.argv):
            if folder_mode:
                print("Warning: --row is not supported in folder mode, ignoring")
            else:
                row = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--limit' and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--workers' and i + 1 < len(sys.argv):
            workers = int(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    try:
        # Get Google credentials
        print("Authenticating with Google...")
        creds = get_credentials()
        print("[OK] Authentication successful\n")

        if folder_mode:
            # Process entire folder
            process_folder(folder_path, spreadsheet_id, creds, sheet_name, limit=limit, workers=workers)
            print("\n[OK] All done!")
        else:
            # Process single file
            download_link = upload_file(media_file)
            filename = os.path.basename(media_file)
            add_link_to_sheet(spreadsheet_id, download_link, creds, sheet_name, row, filename)

            print("\n[OK] All done!")
            print(f"Link: {download_link}")

    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
