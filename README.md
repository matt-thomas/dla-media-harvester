# DLA Media Harvester

A Python CLI tool to harvest audio files (MP3, WAV, M4A, etc.) from CONTENTdm collections (like Berea College’s Digital Library), save them in an organized folder structure, and apply ID3 tags for MP3 files.

## Features

- Query CONTENTdm collections by keyword.
- Searches **all collections by default**; pass `--collection <alias>` to limit to one.
- Downloads audio files, including compound objects (multi-track albums).
- Output directory structure:

  ```
  ./output/<artist>/<collection name> Collection/<song>.<ext>
  ```

- ID3 tagging for MP3s:
  - **Artist** from metadata (e.g. “Primary Performer / Group”)
  - **Album** from collection name (e.g. “Berea College Collection”)
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

### Search all collections

```bash
python get_dla_media.py --query "ernie carpenter" --collection all
```

This will:

- Search **all CONTENTdm collections** on the server for “ernie carpenter”
- Download all available audio files
- Save them in `./output/Ernie Carpenter/Berea College Collection/...`

### Search a specific collection

```bash
python get_dla_media.py --query "ernie carpenter" --collection berea
```

### Common Options

- `--collection berea`
  Restrict search to a single collection (omit to search all).

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
python get_dla_media.py --query "ernie carpenter" --media audio -v
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
