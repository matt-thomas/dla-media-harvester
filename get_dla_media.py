#!/usr/bin/env python3
import argparse, re, time, pathlib, urllib.parse, json
from typing import Dict, Any, List, Optional
import requests
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TCON, TDRC, TCOM, TXXX, COMM
from mutagen.mp3 import MP3

BASE_DEFAULT = "https://dla.contentdm.oclc.org"
OUTPUT_BASE = pathlib.Path("output")

# -------------------------------
# CLI parsing
# -------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download audio from a CONTENTdm collection search and apply ID3 tags."
    )
    p.add_argument("--base", default=BASE_DEFAULT, help="CONTENTdm base URL (default: %(default)s)")
    p.add_argument("--collection", default="berea",
                   help="Collection alias (e.g. 'berea'). If omitted, searches berea.")
    p.add_argument("--query", required=True, help="Search query string")
    p.add_argument("--size", type=int, default=100, help="Items/page (max usually 100)")
    p.add_argument("--max", type=int, default=2000, help="Max records to scan (default: %(default)s)")
    p.add_argument("--delay", type=float, default=0.2, help="Delay between requests (s)")
    p.add_argument("--print-urls", action="store_true",
                   help="Print title + media URL list and exit (no downloads).")
    p.add_argument("--aria2c-list", default=None,
                   help="Write an aria2c input file of URLs (no downloads if used with --print-urls).")
    p.add_argument("--retag", choices=["skip", "update", "overwrite"], default="update",
                   help="ID3 tag policy: 'skip' (don’t modify), 'update' (fill missing), 'overwrite' (replace all).")
    p.add_argument("--dry-run", action="store_true", help="Do everything except write files.")
    p.add_argument("--media", choices=["audio", "mp3"], default="audio",
                   help="Accept any audio/* (default) or only mp3.")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging (show file blobs and decisions).")
    p.add_argument("--dump-json", action="store_true",
                   help="Dump raw JSON metadata into ./_debug_json for inspection.")
    return p.parse_args()

# -------------------------------
# Helpers
# -------------------------------
def unique_path(outdir: pathlib.Path, title: str, ext: str, pointer: str, meta: Dict[str, Any]) -> pathlib.Path:
    """
    Return a unique path for this item within outdir.
    Try plain title first, then add CONTENTdm id, then Identifier field, then numeric suffixes.
    """
    base = safe_filename(title) or "Untitled"
    # 1) plain title
    p = outdir / f"{base}{ext}"
    if not p.exists():
        return p
    # 2) with CONTENTdm pointer/id
    p = outdir / f"{base} (id {pointer}){ext}"
    if not p.exists():
        return p
    # 3) with Identifier field if present
    ident = get_field(meta, ["identi", "identifier"], labels=["Identifier"])
    if ident:
        p2 = outdir / f"{base} ({safe_filename(ident)}){ext}"
        if not p2.exists():
            return p2
    # 4) numeric suffix fallback
    i = 2
    while True:
        p3 = outdir / f"{base} ({i}){ext}"
        if not p3.exists():
            return p3
        i += 1

def _frame_has_text(id3, key: str) -> bool:
    """Return True if the ID3 frame exists AND contains non-empty text."""
    vals = id3.getall(key)
    for v in vals:
        t = getattr(v, "text", None)
        if t:
            s = "".join(t) if isinstance(t, list) else str(t)
            if s.strip():
                return True
    return False

def _set_text(id3, key, cls, val: Optional[str], overwrite: bool):
    """Set a text frame if overwrite=True OR if the existing frame is missing/empty."""
    if not val:
        return
    if overwrite or not _frame_has_text(id3, key):
        id3.setall(key, [cls(encoding=3, text=val)])

def session_with_headers() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "dla-media-harvester/1.1"})
    return s

def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] or "Untitled"

def build_search_url(base: str, coll: str, query: str, page: int, size: int) -> str:
    return (f"{base}/digital/api/search/collection/{coll}"
            f"/searchterm/{urllib.parse.quote(query)}"
            f"/field/all/mode/all/conn/and/page/{page}/size/{size}")

def search_items(base: str, coll: str, query: str, size: int, maxrecs: int, delay: float, s: requests.Session) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page = 1
    while True:
        url = build_search_url(base, coll, query, page, size)
        r = s.get(url, timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        batch = data.get("items") or []
        items.extend(batch)
        if not batch or len(items) >= maxrecs:
            break
        page += 1
        time.sleep(delay)
    return items

def get_item(base: str, coll: str, pointer: str, s: requests.Session) -> Dict[str, Any]:
    url = f"{base}/digital/api/singleitem/collection/{coll}/id/{pointer}"
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def absolute(base: str, url: str) -> str:
    """
    CONTENTdm often returns '/api/...' paths. Those actually live under '/digital'.
    """
    if not url:
        return url
    if url.startswith("http"):
        return url
    if url.startswith("/api/"):
        return f"{base}/digital{url}"
    return f"{base}{url}"

def is_audio_candidate(f: Dict[str, Any], accept_any_audio: bool) -> bool:
    mime = (f.get("mime") or f.get("contentType") or "").lower()
    name = (f.get("name") or f.get("filename") or "").lower()
    if accept_any_audio:
        if mime.startswith("audio/"):
            return True
        if name.endswith((".mp3", ".m4a", ".mp4", ".wav", ".aac", ".aiff", ".aif", ".flac", ".ogg", ".oga")):
            return True
        return False
    else:
        return ("audio/mpeg" in mime) or name.endswith(".mp3")

def _stream_bytes_url(stream_uri: str) -> str:
    # dmGetStreamingFile/.../byte/json → strip trailing '/json' for the raw byte stream
    if not stream_uri:
        return stream_uri
    return stream_uri[:-5] if stream_uri.endswith("/json") else stream_uri

def pick_media(meta: Dict[str, Any], accept_any_audio: bool, verbose: bool, base: str) -> Optional[Dict[str, str]]:
    # Prefer files[]
    files_blob = meta.get("files")
    if files_blob:
        candidates = [f for f in files_blob if is_audio_candidate(f, accept_any_audio)]
        if verbose:
            print("    files:", [{"name": f.get("name"), "mime": f.get("mime")} for f in files_blob])
        if candidates:
            f = candidates[0]
            return {
                "url": absolute(base, f.get("download") or f.get("file")),
                "suggested_name": f.get("name") or "",
            }
    # Then record-level URIs
    if meta.get("downloadUri"):
        return {"url": absolute(base, meta["downloadUri"]), "suggested_name": meta.get("filename") or "file"}
    if meta.get("streamUri"):
        return {"url": _stream_bytes_url(meta["streamUri"]), "suggested_name": meta.get("filename") or "file"}
    return None

def first_nonempty(*vals: Optional[str]) -> Optional[str]:
    for v in vals:
        if v and str(v).strip():
            return str(v).strip()
    return None

def extract_year(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    m = re.search(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b", d)
    return m.group(1) if m else None

def get_field(meta: Dict[str, Any], keys: List[str], labels: List[str] = None) -> Optional[str]:
    """
    Look up a value by key in top-level or by key/label in the 'fields' array.
    Keys/labels are matched case-insensitively.
    """
    # top-level keys
    for k in keys:
        v = meta.get(k)
        if v:
            return v
    # fields array: match by key or label
    labels = labels or []
    keyset = {k.lower() for k in keys}
    labelset = {l.lower() for l in labels}
    for f in meta.get("fields", []):
        fk = (f.get("key") or "").lower()
        fl = (f.get("label") or "").lower()
        val = f.get("value")
        if val and (fk in keyset or fl in labelset):
            return val
    return None

def map_id3_tags(meta: Dict[str, Any], title_fallback: str, source_url: str, holding_library: str) -> Dict[str, str]:
    title = first_nonempty(meta.get("title"), title_fallback) or "Untitled"
    # Artist from Primary Performer/Group (primar), creator, or contributor (any level)
    artist = get_field(meta, ["primar", "creator", "contributor"],
                       labels=["Primary Performer / Group", "Creator", "Contributor"]) or "Unknown Artist"
    album = f"{holding_library} Collection"
    year = extract_year(first_nonempty(meta.get("date"), meta.get("coverage"),
                                       get_field(meta, ["date", "covera"], labels=["Date", "Place"])))
    subject = first_nonempty(meta.get("subject"), get_field(meta, ["subjec"], labels=["Subject"]))
    composer = first_nonempty(meta.get("creator"), get_field(meta, ["creator"], labels=["Creator"]))
    rights = first_nonempty(meta.get("rights"), get_field(meta, ["rights"], labels=["Rights"]))
    desc = first_nonempty(meta.get("description"), meta.get("descri"),
                          get_field(meta, ["descri", "description"], labels=["Description"]))

    comment_lines = []
    if desc: comment_lines.append(desc)
    comment_lines.append(f"Source: {source_url}")
    if rights: comment_lines.append(f"Rights: {rights}")

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "year": year or "",
        "genre": subject or "",
        "composer": composer or "",
        "comment": "\n".join(comment_lines),
        "source_url": source_url,
        "cdm_id": str(meta.get("id") or meta.get("pointer") or ""),
    }

def apply_id3(path: pathlib.Path, tags: Dict[str, str], policy: str) -> str:
    if policy == "skip":
        return "skipped"

    audio = MP3(path)
    try:
        id3 = audio.tags or ID3(path)  # prefer the tag bound to this MP3
    except ID3NoHeaderError:
        id3 = ID3()

    def existing(k: str) -> bool:
        return k in id3 and bool(id3.get(k))
    def set_text(key, cls, val: Optional[str], overwrite: bool):
        if not val: return
        if overwrite or not existing(key):
            id3.setall(key, [cls(encoding=3, text=val)])

    overwrite = (policy == "overwrite")

    set_text("TIT2", TIT2, tags.get("title"), overwrite)
    set_text("TPE1", TPE1, tags.get("artist"), overwrite)
    set_text("TALB", TALB, tags.get("album"), overwrite)
    set_text("TCON", TCON, tags.get("genre"), overwrite)
    set_text("TCOM", TCOM, tags.get("composer"), overwrite)

    # Year
    yr = tags.get("year")
    if yr and (overwrite or not _frame_has_text(id3, "TDRC")):
        id3.setall("TDRC", [TDRC(encoding=3, text=yr)])

    # Comment
    comment_text = tags.get("comment")
    if comment_text and (overwrite or not _frame_has_text(id3, "COMM")):
        id3.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=comment_text)])

    # TXXX frames: overwrite if present and we’re in overwrite mode
    if overwrite:
        # remove existing SOURCE_URL / CONTENTDM_ID frames to avoid duplicates
        for frame in list(id3.getall("TXXX")):
            desc = getattr(frame, "desc", "").upper()
            if desc in {"SOURCE_URL", "CONTENTDM_ID"}:
                id3.delall("TXXX")
                break
    if tags.get("source_url"):
        id3.add(TXXX(encoding=3, desc="SOURCE_URL", text=tags["source_url"]))
    if tags.get("cdm_id"):
        id3.add(TXXX(encoding=3, desc="CONTENTDM_ID", text=tags["cdm_id"]))

    audio.tags = id3
    audio.save(v2_version=3, v1=2)
    return "overwritten" if overwrite else "updated"

# -------------------------------
# Collection display-name map (fallback if holdin missing)
# -------------------------------
def get_collection_map(base: str, s: requests.Session) -> Dict[str, str]:
    url = f"{base}/digital/bl/dmwebservices/index.php?q=dmGetCollectionList/json"
    try:
        r = s.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        mapping = {}
        for c in data:
            alias = c.get("alias")
            name = c.get("name")
            if alias and name:
                mapping[alias] = name
        return mapping
    except Exception as e:
        print(f"[warn] cannot fetch collection list: {e}")
        return {}

# -------------------------------
# Main
# -------------------------------
def main():
    args = parse_args()
    s = session_with_headers()

    coll_for_search = args.collection  # default "berea" per CLI
    coll_map = get_collection_map(args.base, s)

    items = search_items(args.base, coll_for_search, args.query, args.size, args.max, args.delay, s)
    if not items:
        print("No results found.")
        return
    print(f"Found {len(items)} items for query: {args.query!r} in collection {coll_for_search!r}\n")

    downloaded = 0
    tagged_counts = {"skipped": 0, "updated": 0, "overwritten": 0}
    accept_any_audio = (args.media == "audio")

    for it in items:
        alias = it.get("collectionAlias") or args.collection or "unknown"
        pointer = it.get("itemId") or it.get("id") or it.get("pointer")
        title = it.get("title") or f"item_{pointer}"
        if not pointer or not alias:
            print(f"[skip] cannot extract pointer for title='{title}'  item_keys={list(it.keys())}")
            continue

        try:
            meta = get_item(args.base, alias, str(pointer), s)
        except Exception as e:
            print(f"[skip] {title}: cannot fetch singleitem ({e})")
            continue

        picked = pick_media(meta, accept_any_audio, args.verbose, base=args.base)
        if not picked:
            print(f"[skip] alias={alias} id={pointer}  title='{title}'\n       files: none")
            continue

        # Resolve Holding Library -> album name & directory
        holding_library = get_field(
            meta,
            ["holdin"],  # key
            labels=["Holding Library"]  # label
        ) or coll_map.get(alias, alias)

        # Artist for directory (use same logic as tag)
        artist = get_field(meta, ["primar", "creator", "contributor"],
                           labels=["Primary Performer / Group", "Creator", "Contributor"]) or "Unknown Artist"
        album = f"{holding_library} Collection"

        suffix = pathlib.Path(picked['suggested_name']).suffix.lower() or ".mp3"

        outdir = OUTPUT_BASE / safe_filename(artist) / safe_filename(album)
        if not args.dry_run:
            outdir.mkdir(parents=True, exist_ok=True)

        path = unique_path(outdir, title, suffix, str(pointer), meta)

        media_url = absolute(args.base, picked["url"])
        if args.print_urls:
            print(f"{title}\n{media_url}\n")
            if args.aria2c_list:
                with open(args.aria2c_list, "a", encoding="utf-8") as fh:
                    fh.write(media_url + "\n")
            continue

        if not args.dry_run:
            if not path.exists():
                try:
                    with s.get(media_url, stream=True, timeout=120) as r:
                        r.raise_for_status()
                        with open(path, "wb") as f:
                            for chunk in r.iter_content(1024 * 64):
                                if chunk:
                                    f.write(chunk)
                    print(f"[ok] {path}")
                    downloaded += 1
                except Exception as e:
                    print(f"[error] {title}: {e}")
                    continue
            else:
                print(f"[exists] {path}")

        # ID3 tagging for MP3 only
        if not args.dry_run and path.exists() and path.suffix.lower() == ".mp3":
            try:
                source_page = meta.get("find") or f"{args.base}/digital/collection/{alias}/id/{pointer}"
                tags = map_id3_tags(meta, title, source_page, holding_library)
                result = apply_id3(path, tags, policy=args.retag)
                tagged_counts[result] += 1
            except Exception as e:
                print(f"[tag] {filename}: {e}")

        time.sleep(args.delay)

    print(f"\nDone. Downloaded {downloaded} file(s) into: {OUTPUT_BASE}")
    if args.retag != "skip":
        print(f"ID3 tagging — updated: {tagged_counts['updated']}, overwritten: {tagged_counts['overwritten']}, skipped: {tagged_counts['skipped']}")

if __name__ == "__main__":
    main()
