#!/usr/bin/env python3
"""
Berea CONTENTdm Audio Downloader

- Outputs to ./output/<artist>/<album>/<song>.<ext>
- Tags MP3s with artist/album/song (ID3 via mutagen)
- Album name = "<College Name> Collection" (passable via --college-name)
- Auto-detects per-item collection alias + ID from search results
- Handles compound objects (albums): fetches children and their media
- Supports singleitem JSON with `downloadUri` / `streamUri`
- Falls back to classic `files` array if present
"""

from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import textwrap
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TCON, TDRC, TCOM, TXXX, COMM
from mutagen.mp3 import MP3

BASE_DEFAULT = "https://dla.contentdm.oclc.org"
FIND_RE = re.compile(r"/digital/collection/([^/]+)/id/(\d+)")

# ------------- CLI -------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download audio from a CONTENTdm collection search (handles compound objects) and tag MP3s."
    )
    p.add_argument("--base", default=BASE_DEFAULT, help="CONTENTdm base URL (default: %(default)s)")
    p.add_argument("--collection", default="berea", help="Default collection alias for search (fallback only)")
    p.add_argument("--query", required=True, help="Search query string")
    p.add_argument("--size", type=int, default=100, help="Items/page for search (max usually 100)")
    p.add_argument("--max", type=int, default=2000, help="Max records to scan")
    p.add_argument("--delay", type=float, default=0.2, help="Delay between requests (seconds)")

    # New: Output root and album naming
    p.add_argument("--output-root", default="output",
                   help="Root output directory (default: %(default)s)")
    p.add_argument("--college-name", default="Berea College",
                   help='College/collection label used to build album as "<College Name> Collection" (default: %(default)s)')

    # accept hyphen/underscore forms; store as underscores
    p.add_argument("--print-urls", "--print_urls", dest="print_urls", action="store_true",
                   help="Print title + media URL list and exit (no downloads).")
    p.add_argument("--aria2c-list", "--aria2c_list", dest="aria2c_list", default=None,
                   help="Write an aria2c input file of URLs (no downloads if used with --print-urls).")

    p.add_argument("--retag", choices=["skip", "update", "overwrite"], default="update",
                   help="ID3 tag policy for MP3s: skip | update | overwrite")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="Do everything except write files.")
    p.add_argument("--media", choices=["audio", "mp3"], default="audio",
                   help="Accept any audio/* (default) or only mp3.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Extra logging (candidate decisions, headers).")
    p.add_argument("--dump-json", action="store_true",
                   help="Dump each parent/child raw JSON to _debug_json/<alias>_<id>.json for inspection.")
    return p.parse_args()

# ------------- HTTP -------------
def session_with_headers() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "cdm-audio-cli/1.5 (+noncommercial)",
        "Accept": "application/json, */*;q=0.5",
    })
    return s

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
            print(f"[warn] search HTTP {r.status_code} for {url}")
            break
        data = r.json()
        batch = data.get("items") or []
        items.extend(batch)
        if not batch or len(items) >= maxrecs:
            break
        page += 1
        time.sleep(delay)
    return items

def get_item(base: str, coll_alias: str, pointer: str, s: requests.Session) -> Dict[str, Any]:
    url = f"{base}/digital/api/singleitem/collection/{coll_alias}/id/{pointer}"
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def _looks_json_response(r: requests.Response) -> bool:
    ctype = (r.headers.get("Content-Type") or "").lower()
    return "application/json" in ctype or "json" in ctype

def get_compound_children(base: str, coll_alias: str, pointer: str, s: requests.Session) -> List[Dict[str, Any]]:
    """
    Fetch child list for a compound (album) object.
    Correct canonical endpoint includes 'collection/{alias}/id/{id}'.
    """
    candidates = [
        f"{base}/digital/api/compound/object/collection/{coll_alias}/id/{pointer}",
        f"{base}/digital/api/compound/object/collection/{coll_alias}/{pointer}",
        f"{base}/digital/api/compound/object/collection/{coll_alias}/id/{pointer}/",
        # old/odd variants as last resort:
        f"{base}/digital/api/compound/object/{coll_alias}/id/{pointer}",
        f"{base}/digital/api/compound/object/{coll_alias}/{pointer}",
        f"{base}/digital/api/compound/object/{coll_alias}/id/{pointer}/",
    ]
    referer = f"{base}/digital/collection/{coll_alias}/id/{pointer}"

    for url in candidates:
        try:
            r = s.get(url, timeout=30, headers={"Referer": referer})
        except Exception as e:
            print(f"[warn] compound GET failed {url}: {e}")
            continue

        if r.status_code == 404:
            continue
        if r.status_code != 200:
            print(f"[warn] compound HTTP {r.status_code} for {url}")
            continue
        if not _looks_json_response(r):
            snippet = (r.text or "")[:160].replace("\n", " ")
            print(f"[warn] compound non-JSON at {url}: {snippet!r}")
            continue

        try:
            data = r.json()
        except ValueError:
            print(f"[warn] compound JSON parse failed at {url}")
            continue

        children = data.get("children") or []
        return children if isinstance(children, list) else []
    return []

# ------------- Utilities -------------
def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] or "Untitled"

def pretty(obj: Any, width: int = 100) -> str:
    try:
        s = json.dumps(obj, indent=2, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return "\n".join(textwrap.wrap(s, width=width)) if "\n" not in s and len(s) > width else s

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

def ensure_root(out_root: str) -> pathlib.Path:
    p = pathlib.Path(out_root).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def extract_alias_and_pointer(item: dict, default_alias: str) -> Tuple[str, Optional[str]]:
    """
    Prefer new-style fields from CONTENTdm search:
      - collectionAlias
      - itemId
    Fallbacks:
      - parse from itemLink/find/link using regex
      - default_alias + legacy id/pointer/dmrecord
    """
    alias = item.get("collectionAlias")
    pointer = item.get("itemId")
    if alias and pointer:
        return str(alias), str(pointer)

    for k in ("itemLink", "find", "link", "itemLinkUrl"):
        v = item.get(k)
        if isinstance(v, str):
            m = FIND_RE.search(v)
            if m:
                return m.group(1), m.group(2)

    legacy_pointer = item.get("id") or item.get("pointer") or item.get("dmrecord")
    return (alias or default_alias), (str(legacy_pointer) if legacy_pointer is not None else None)

def child_alias_and_pointer(child: Dict[str, Any], parent_alias: str) -> Tuple[str, Optional[str]]:
    alias = child.get("collectionAlias") or parent_alias
    pointer = child.get("itemId") or child.get("id") or child.get("dmrecord")
    if pointer:
        return str(alias), str(pointer)
    for k in ("itemLink", "find", "link", "itemLinkUrl"):
        v = child.get(k)
        if isinstance(v, str):
            m = FIND_RE.search(v)
            if m:
                return m.group(1), m.group(2)
    return alias, None

# ------------- Media picking & tagging -------------
def is_audio_candidate_by_name_or_mime(name: str, mime: str, accept_any_audio: bool) -> bool:
    name = (name or "").lower()
    mime = (mime or "").lower()
    if accept_any_audio:
        if mime.startswith("audio/"):
            return True
        if name.endswith((".mp3", ".m4a", ".mp4", ".wav", ".aac", ".aiff", ".aif", ".flac", ".ogg", ".oga")):
            return True
        return False
    else:
        return ("audio/mpeg" in mime) or name.endswith(".mp3")

def pick_media_from_files(files_blob: Optional[List[Dict[str, Any]]], accept_any_audio: bool, verbose: bool) -> Optional[Dict[str, str]]:
    if not files_blob:
        return None
    candidates = []
    for f in files_blob:
        if is_audio_candidate_by_name_or_mime(f.get("name",""), f.get("mime",""), accept_any_audio):
            url = f.get("download") or f.get("file")
            if url:
                candidates.append({
                    "url": url,
                    "suggested_name": f.get("name") or "",
                    "mime": f.get("mime") or ""
                })
    if verbose:
        print("    files:", [{"name": f.get("name"), "mime": f.get("mime"),
                              "download": bool(f.get("download")), "file": bool(f.get("file"))}
                             for f in (files_blob or [])])
        print("    candidates:", [{"name": c["suggested_name"], "mime": c["mime"]} for c in candidates])
    return candidates[0] if candidates else None

def absolute(base: str, url: str) -> str:
    if not url:
        return url
    if url.startswith("http"):
        return url
    # CONTENTdm JSON often returns '/api/...' paths without '/digital' prefix.
    prefix = "/digital" if not url.startswith("/digital") else ""
    return f"{base}{prefix}{url}"

def resolve_stream_url(stream_uri: str) -> str:
    # dmwebservices .../byte/json → remove trailing /json for raw byte stream
    if not stream_uri:
        return ""
    return stream_uri[:-5] if stream_uri.endswith("/json") else stream_uri

def pick_media_from_singleitem_meta(meta: Dict[str, Any], accept_any_audio: bool, verbose: bool) -> Optional[Dict[str, str]]:
    name = meta.get("filename") or ""
    mime = meta.get("contentType") or ""
    download_uri = meta.get("downloadUri")
    stream_uri = meta.get("streamUri")

    if download_uri and is_audio_candidate_by_name_or_mime(name, mime, accept_any_audio):
        return {"url": absolute(meta.get("_base_override") or BASE_DEFAULT, download_uri),
                "suggested_name": name or "",
                "mime": mime or ""}

    if stream_uri:
        stream_url = resolve_stream_url(stream_uri)
        suggested = name or pathlib.Path(stream_url.split("?")[0]).name or "audio"
        return {"url": stream_url, "suggested_name": suggested, "mime": mime or ""}

    return None

def map_id3_tags(meta: Dict[str, Any], title_fallback: str, source_url: str,
                 album_override: Optional[str]) -> Dict[str, str]:
    # Try direct fields first
    title = first_nonempty(meta.get("title"), title_fallback) or "Untitled"
    artist = first_nonempty(meta.get("creator"), meta.get("contributor"))
    subject = first_nonempty(meta.get("subject"))
    desc = first_nonempty(meta.get("description"))
    rights = first_nonempty(meta.get("rights"))
    album = album_override or first_nonempty(meta.get("collection"), meta.get("publisher"), "CONTENTdm Audio")
    year = extract_year(first_nonempty(meta.get("date"), meta.get("coverage")))
    composer = first_nonempty(meta.get("creator"))

    # If using field list (modern schema), mine likely labels
    fields = meta.get("fields")
    if isinstance(fields, list):
        by_label = { (f.get("label") or "").lower(): f.get("value") for f in fields }
        title = first_nonempty(title, by_label.get("title"))
        artist = first_nonempty(artist, by_label.get("primary performer / group"), by_label.get("creator"))
        subject = first_nonempty(subject, by_label.get("subject"))
        desc = first_nonempty(desc, by_label.get("description"))
        rights = first_nonempty(rights, by_label.get("rights"))
        # album from override wins; otherwise try Relation/Holding Library
        album = album_override or first_nonempty(album, by_label.get("relation"), by_label.get("holding library"))

    comment_lines = []
    if desc:
        comment_lines.append(desc)
    comment_lines.append(f"Source: {source_url}")
    if rights:
        comment_lines.append(f"Rights: {rights}")

    return {
        "title": title or "Untitled",
        "artist": artist or "Unknown Artist",
        "album": album or "Unknown Album",
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
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()

    def existing(k: str) -> bool:
        return k in id3 and bool(id3.get(k))
    def set_text(key, cls, val: Optional[str], overwrite: bool):
        if not val:
            return
        if overwrite or not existing(key):
            id3.setall(key, [cls(encoding=3, text=val)])

    overwrite = (policy == "overwrite")
    set_text("TIT2", TIT2, tags.get("title"), overwrite)   # song
    set_text("TPE1", TPE1, tags.get("artist"), overwrite)  # artist
    set_text("TALB", TALB, tags.get("album"), overwrite)   # album
    set_text("TCON", TCON, tags.get("genre"), overwrite)
    set_text("TCOM", TCOM, tags.get("composer"), overwrite)
    yr = tags.get("year")
    if yr and (overwrite or not existing("TDRC")):
        id3.setall("TDRC", [TDRC(encoding=3, text=yr)])
    comment_text = tags.get("comment")
    if comment_text and (overwrite or not existing("COMM")):
        id3.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=comment_text)])

    # Custom TXXX frames (source URL & ContentDM ID)
    if overwrite:
        for f in list(id3.getall("TXXX")):
            if getattr(f, "desc", "").upper() in {"SOURCE_URL", "CONTENTDM_ID"}:
                id3.delall("TXXX")
                break
    if tags.get("source_url"):
        id3.add(TXXX(encoding=3, desc="SOURCE_URL", text=tags["source_url"]))
    if tags.get("cdm_id"):
        id3.add(TXXX(encoding=3, desc="CONTENTDM_ID", text=tags["cdm_id"]))

    id3.save(path)
    audio.save()
    return "overwritten" if overwrite else "updated"

# ------------- Output path builder -------------
def path_for_track(root: pathlib.Path, tags: Dict[str, str], ext: str) -> pathlib.Path:
    artist_dir = safe_filename(tags.get("artist") or "Unknown Artist")
    album_dir  = safe_filename(tags.get("album")  or "Unknown Album")
    song_name  = safe_filename(tags.get("title")  or "Untitled")
    dest_dir = root / artist_dir / album_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / f"{song_name}{ext}"

def dedupe_path(p: pathlib.Path) -> pathlib.Path:
    """Append (1), (2), ... if a file already exists."""
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 1
    while True:
        candidate = p.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1

# ------------- Main -------------
def main():
    args = parse_args()
    s = session_with_headers()

    # Output root
    out_root = ensure_root(args.output_root)

    if args.dump_json:
        pathlib.Path("_debug_json").mkdir(exist_ok=True)

    items = search_items(args.base, args.collection, args.query, args.size, args.max, args.delay, s)
    if not items:
        print("No results found.")
        return

    print(f"Found {len(items)} items for query: {args.query!r} in collection {args.collection!r}\n")

    if args.dump_json:
        with open("_debug_json/_search_items.json", "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)

    aria_lines: List[str] = []
    printed = 0
    downloaded = 0
    tagged_counts = {"skipped": 0, "updated": 0, "overwritten": 0}
    accept_any_audio = (args.media == "audio")
    album_override = f"{args.college_name} Collection".strip()

    for it in items:
        alias, pointer = extract_alias_and_pointer(it, args.collection)
        title = it.get("title") or f"item_{pointer or 'UNKNOWN'}"

        if not pointer:
            print(f"[skip] cannot extract pointer for title={title!r}  item_keys={list(it.keys())}")
            if args.dump_json:
                with open(f"_debug_json/_search_item_{safe_filename(title)[:60]}.json", "w", encoding="utf-8") as f:
                    json.dump(it, f, indent=2, ensure_ascii=False)
            continue

        # Fetch parent (may be compound, or a flat audio record)
        try:
            meta = get_item(args.base, alias, pointer, s)
        except Exception as e:
            print(f"[error] singleitem fetch failed alias={alias} id={pointer} title={title!r}: {e}")
            continue

        meta["_base_override"] = args.base

        if args.dump_json:
            with open(f"_debug_json/{alias}_{pointer}.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

        if args.verbose:
            print(f"[meta] alias={alias} id={pointer} downloadUri={meta.get('downloadUri')!r} streamUri={meta.get('streamUri')!r}")

        picked = None

        # 1) Try modern singleitem (downloadUri/streamUri)
        picked = pick_media_from_singleitem_meta(meta, accept_any_audio, args.verbose)

        # 2) Classic 'files' array (older installs)
        if not picked:
            picked = pick_media_from_files(meta.get("files"), accept_any_audio, args.verbose)

        # 3) Compound children (albums)
        if not picked:
            children = get_compound_children(args.base, alias, pointer, s)
            if children:
                print(f"[info] alias={alias} id={pointer} title={title!r} — compound object with {len(children)} child(ren)")
                for idx, ch in enumerate(children, 1):
                    ch_alias, ch_pointer = child_alias_and_pointer(ch, alias)
                    if not ch_pointer:
                        print(f"       [child {idx}] no pointer; keys={list(ch.keys())}")
                        continue
                    try:
                        ch_meta = get_item(args.base, ch_alias, ch_pointer, s)
                        ch_meta["_base_override"] = args.base
                    except Exception as e:
                        print(f"       [child {idx}] fetch failed alias={ch_alias} id={ch_pointer}: {e}")
                        continue

                    if args.dump_json:
                        with open(f"_debug_json/{ch_alias}_{ch_pointer}.json", "w", encoding="utf-8") as f:
                            json.dump(ch_meta, f, indent=2, ensure_ascii=False)

                    picked = pick_media_from_singleitem_meta(ch_meta, accept_any_audio, args.verbose)
                    if not picked:
                        picked = pick_media_from_files(ch_meta.get("files"), accept_any_audio, args.verbose)

                    if picked:
                        child_title = ch_meta.get("title") or ch.get("title") or f"{title} (part {idx})"
                        if child_title and child_title not in title:
                            title = f"{title} — {child_title}"
                        alias, pointer = ch_alias, ch_pointer
                        meta = ch_meta  # use child metadata for tagging/path
                        break

        # If still nothing, skip with diagnostics
        if not picked:
            print(f"[skip] alias={alias} id={pointer}  title={title!r}")
            have_files = meta.get("files")
            print("       files:", "none" if not have_files else pretty(have_files)[:800])
            source_page = meta.get("find") or f"{args.base}/digital/collection/{alias}/id/{pointer}"
            print(f"       source: {source_page}")
            print("       hint: record may be streaming-only or restricted; see JSON in _debug_json/")
            print()
            continue

        # Build tags now so we can build the *path* from artist/album/song
        source_page = meta.get("find") or f"{args.base}/digital/collection/{alias}/id/{pointer}"
        tags = map_id3_tags(meta, title, source_page, album_override=album_override)

        # Build output path: ./output/<artist>/<album>/<song>.<ext>
        media_url = picked["url"] if picked["url"].startswith("http") else absolute(args.base, picked["url"])
        suggested = picked.get("suggested_name") or "audio"
        if not pathlib.Path(suggested).suffix:
            # best effort extension from mime
            ext = ".mp3" if (args.media == "mp3" or "mpeg" in (picked.get("mime","").lower())) else ".bin"
        else:
            ext = pathlib.Path(suggested).suffix.lower()
        dest_path = path_for_track(out_root, tags, ext)
        dest_path = dedupe_path(dest_path)

        print(f"[pick] alias={alias} id={pointer} -> {dest_path}  mime={picked.get('mime','')!r}  url={media_url}")

        if args.print_urls:
            print(f"{tags['title']}\n{media_url}\n")
            printed += 1
            if args.aria2c_list:
                aria_lines.append(media_url)
            continue

        if not args.dry_run:
            if dest_path.exists():
                print(f"  [exists] {dest_path.name}")
            else:
                try:
                    with s.get(media_url, stream=True, timeout=120) as r:
                        r.raise_for_status()
                        with open(dest_path, "wb") as f:
                            for chunk in r.iter_content(1024 * 64):
                                if chunk:
                                    f.write(chunk)
                    print(f"  [ok] {dest_path.name}")
                    downloaded += 1
                except Exception as e:
                    print(f"  [error] {tags['title']}: {e}")
                    continue

        # Tag MP3s only — using the same tags we used to build the path
        if not args.dry_run and dest_path.exists() and dest_path.suffix.lower() == ".mp3":
            try:
                result = apply_id3(dest_path, tags, policy=args.retag)
                tagged_counts[result] += 1
                print(f"  [tag] {dest_path.name}: {result}")
            except Exception as e:
                print(f"  [tag] {dest_path.name}: {e}")

        time.sleep(args.delay)

    if args.aria2c_list:
        with open(args.aria2c_list, "w", encoding="utf-8") as f:
            f.write("\n".join(aria_lines))
        print(f"\nWrote aria2c URL list: {args.aria2c_list}")

    if args.print_urls:
        print(f"\nPrinted {printed} media URL(s).")
    else:
        print(f"\nDone. Downloaded {downloaded} file(s) to: {out_root}")
        if args.retag != "skip":
            print(f"ID3 tagging — updated: {tagged_counts['updated']}, overwritten: {tagged_counts['overwritten']}, skipped: {tagged_counts['skipped']}")

if __name__ == "__main__":
    main()
