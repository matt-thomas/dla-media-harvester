# dla-media-harvester

A command-line tool to download audio recordings from CONTENTdm collections (such as Berea College’s Southern Appalachian Archives) and tag them with proper ID3 metadata.

---

## Features
- Search a CONTENTdm collection by keyword.
- Download audio files (MP3 or other audio formats).
- Organize output by **Artist → Collection → Song**.
- Tag ID3 metadata including **Artist, Album, Title, Year, Source URL, and Rights**.
- Optionally re-tag existing files without redownloading (`--retag`).
- Support for multiple CONTENTdm collections.

---

## Installation

Clone this repo and install dependencies:

```bash
git clone https://github.com/matt-thomas/dla-media-harvester.git
cd dla-media-harvester
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

Dependencies:
- `requests`
- `mutagen`

---

## Usage

### Basic search (default = Berea College collection)

If you don’t specify a collection, the default is **Berea College**:

```bash
python get_dla_media.py --query "ernie carpenter"
```

This will download files into:

```
output/<Artist>/<Holding Library Collection>/<Song>.mp3
```

Example:

```
output/Carpenter, Ernie/Berea College Collection/Camp Run.mp3
```

### Search a specific collection

To search another CONTENTdm collection, pass the collection alias:

```bash
python get_dla_media.py --collection duluth --query "bluegrass"
```

### Re-tag existing files without redownloading

```bash
python get_dla_media.py --query "ernie carpenter" --retag overwrite
```

This will scan matching items and **update ID3 tags** even if files already exist.

### Print URLs only

```bash
python get_dla_media.py --query "banjo" --print-urls
```

### Dump raw JSON metadata for inspection

```bash
python get_dla_media.py --query "fiddle" --dump-json
```

---

## Output structure

Files are saved into:

```
output/<Artist>/<Holding Library Collection>/<Song>.mp3
```

Where:
- **Artist** = primary performer / creator / contributor.
- **Holding Library Collection** = e.g. `Berea College Collection`.
- **Song** = recording title.

---

## Notes
- Only audio files (`.mp3`, `.wav`, `.m4a`, etc.) are downloaded.
- Metadata is pulled from CONTENTdm’s `fields` (e.g., *Title*, *Primary Performer / Group*, *Holding Library*).
- ID3 tags default to version 2.3 for maximum compatibility.
