# DLA Media Harvester

A Python CLI tool to harvest audio files (MP3, WAV, M4A, etc.) from CONTENTdm collections (like Berea College’s Digital Library), save them in an organized folder structure, and apply ID3 tags for MP3 files.

## Features

- Query CONTENTdm collections by keyword.
- Downloads audio files, including compound objects (multi-track albums).
- Output directory structure:

  ```
  ./output/<artist>/<album>/<song>.<ext>
  ```

- ID3 tagging for MP3s:
  - **Artist** from metadata (e.g. “Primary Performer / Group”)
  - **Album** from `--college-name` (e.g. “Berea College Collection”)
  - **Song title** from metadata (“Title” field)
- Fallbacks ensure tags are never blank.
- Verbose mode to debug media selection.
- JSON dump for inspection of raw CONTENTdm responses.
- Aria2c list output for bulk downloading.

## Installation

Create a virtual environment and install dependencies:

```bash
python -m venv env
source env/bin/activate
pip install -r requirements.txt
```

## Usage

Basic usage:

```bash
python get_dla_media.py --query "ernie carpenter" --college-name "Berea College"
```

This will:

- Search Berea’s CONTENTdm collection for “ernie carpenter”
- Download all available audio files
- Save them in `./output/Ernie Carpenter/Berea College Collection/...`

### Common Options

- `--college-name "Berea College"`  
  Sets the album tag to `"<College Name> Collection"` and names the album folder accordingly.

- `--media audio`  
  Accepts any audio type (MP3, WAV, M4A, etc.).  
  Use `--media mp3` to restrict downloads to MP3 only.

- `--print-urls`  
  Print discovered media URLs without downloading.

- `--aria2c-list urls.txt`  
  Write an aria2c input file for batch downloading.

- `--dump-json`  
  Save raw JSON for each item in `_debug_json/`.

- `-v`  
  Verbose logging — shows file candidates and decisions.

- `--dry-run`  
  Simulate actions without writing files.

### Example Commands

Download and organize all Ernie Carpenter tracks into structured folders:

```bash
python get_dla_media.py --query "ernie carpenter" \
  --college-name "Berea College" \
  --media audio -v
```

Only print URLs for external downloading:

```bash
python get_dla_media.py --query "ernie carpenter" --print-urls
```

Generate aria2c input list:

```bash
python get_dla_media.py --query "ernie carpenter" --aria2c-list ernie_urls.txt
```

Inspect raw JSON responses for debugging:

```bash
python get_dla_media.py --query "ernie carpenter" --dump-json -v
```

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**.  
See [LICENSE](https://www.gnu.org/licenses/gpl-3.0.en.html) for details.
