# Berea CONTENTdm Audio Downloader

A command-line tool to fetch audio files (MP3, M4A, MP4, WAV, etc.) from a [CONTENTdm](https://www.oclc.org/en/contentdm.html) collection search (e.g. Berea College’s Digital Library), and apply smart ID3 tagging to MP3s based on metadata.

It uses the public CONTENTdm JSON API, requires no login, and can bulk-download matching audio files with sensible filenames and tags.

---

## Features
- Search any CONTENTdm collection by query term (default: Berea College’s `berea` collection).  
- Bulk download all matching audio files (not just MP3).  
- Optionally just print URLs or export an `aria2c` download list.  
- Smart ID3 tagging using [mutagen](https://mutagen.readthedocs.io/) (applied **only to MP3s**):  
  - **Title** from record title  
  - **Artist** from `creator` or `contributor`  
  - **Album** from `collection` or `publisher`  
  - **Year** extracted from `date`  
  - **Genre** from `subject`  
  - **Composer** from `creator` (if applicable)  
  - **Comment** includes description, rights, and source URL  
  - Adds `TXXX` custom frames for CONTENTdm ID and source page  

---

## Requirements
Python 3.8+  
Install dependencies with:

```bash
pip install -r requirements.txt
```

`requirements.txt`:

```
requests>=2.31
mutagen>=1.47
```

---

## Quick Start

### 1. Clone and set up
```bash
git clone https://github.com/yourname/berea-cdm-audio.git
cd berea-cdm-audio
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Download Ernie Carpenter recordings
```bash
python berea_cdm_mp3.py --query "ernie carpenter"
```
This creates a folder named `ernie carpenter_CONTENTdm_Audio/` and saves audio files.

### 3. Just get URLs (no downloads)
```bash
python berea_cdm_mp3.py --query "ernie carpenter" --print-urls
```

### 4. Prepare an aria2c batch download
```bash
python berea_cdm_mp3.py --query "ernie carpenter" --print-urls --aria2c-list urls.txt
aria2c -i urls.txt -d ernie_carpenter_Audio
```

### 5. Control ID3 tagging (MP3 files only)
```bash
# Default: update missing tags only
python berea_cdm_mp3.py --query "ernie carpenter" --retag update

# Overwrite all tags with metadata
python berea_cdm_mp3.py --query "ernie carpenter" --retag overwrite

# Skip tagging completely
python berea_cdm_mp3.py --query "ernie carpenter" --retag skip
```

---

## Options
```
--base           CONTENTdm base URL (default: https://dla.contentdm.oclc.org)
--collection     Collection alias (default: berea)
--query          Search query string (required)
--outdir         Output directory (default: "<query>_CONTENTdm_Audio")
--print-urls     Print title + media URL list, no downloads
--aria2c-list    Write an aria2c input file of URLs
--retag          ID3 tag policy: skip | update | overwrite (default: update)
--dry-run        Simulate actions, no files written
--delay          Delay between requests (default: 0.2s)
--size           Page size for API requests (default: 100)
--max            Max records to fetch (default: 2000)
--media          Acceptable media: audio (any audio/*, default) | mp3 (only MP3s)
--verbose, -v    Verbose logging (show file blobs and candidates)
```

---

## Verifying Tags
To confirm tags after download:

```bash
python - <<'PY'
from mutagen.id3 import ID3
from pathlib import Path
for mp3 in Path("ernie carpenter_CONTENTdm_Audio").glob("*.mp3"):
    try:
        id3 = ID3(mp3)
        print(mp3.name, id3.get("TIT2"), id3.get("TPE1"), id3.get("TALB"), id3.get("TDRC"))
    except Exception as e:
        print(mp3.name, e)
PY
```

Or use a GUI tag editor like [Kid3](https://kid3.sourceforge.io/) or [Mp3tag](https://www.mp3tag.de/en/).

---

## License
This project is licensed under the **GNU General Public License v3.0**.  
See the [LICENSE](https://www.gnu.org/licenses/gpl-3.0.en.html) file for details.
