#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4==4.14.3", "requests==2.32.5"]
# ///
"""Snapshot public Notion formatting omitted by its HTML export.

    uv run scripts/notion_metadata.py EXPORT notion-metadata.json \
        --notion-site https://example.notion.site

Only public block records are read, without login cookies or credentials.
The snapshot contains hashes, wrapping settings and annotation ranges, not
Notion's application data or a second copy of the article text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

GENERATOR = "notion_export_metadata"
SCHEMA = 1
COLORS = {"default", "gray", "brown", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "red"}
STYLES = {"b": "notion-code-bold", "i": "notion-code-italic", "s": "notion-code-strike", "_": "notion-code-underline"}


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def block_id(pre):
    # Captioned code has its block ID on the enclosing figure.
    owner = pre if pre.get("id") else pre.find_parent("figure", id=True)
    return owner.get("id") if owner else None


def annotation_classes(annotations):
    classes = []
    for annotation in annotations:
        if not isinstance(annotation, list) or not annotation:
            raise ValueError("Invalid code annotation")
        kind = annotation[0]
        if kind in STYLES and len(annotation) == 1:
            classes.append(STYLES[kind])
        elif kind == "h" and len(annotation) == 2 and isinstance(annotation[1], str):
            color = annotation[1]
            if color.removesuffix("_background") not in COLORS:
                raise ValueError(f"Unknown Notion annotation color: {color}")
            classes.append("highlight-" + color)
        else:
            raise ValueError(f"Unsupported code annotation: {annotation!r}")
    return classes


def read_metadata(path):
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("generator") != GENERATOR or data.get("schema") != SCHEMA:
        raise ValueError("Unrecognized Notion metadata snapshot. Regenerate it with notion_metadata.py.")
    if data.get("offset_unit") != "unicode-code-points":
        raise ValueError("Metadata ranges must explicitly use unicode-code-points")
    blocks = data.get("code")
    if not isinstance(blocks, dict):
        raise ValueError("Metadata code entries must be an object")
    for bid, entry in blocks.items():
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise ValueError(f"Invalid metadata hash for {bid}")
        if type(entry.get("wrap")) is not bool or not isinstance(entry.get("ranges"), list):
            raise ValueError(f"Invalid code formatting for {bid}")
        end = 0
        for span in entry["ranges"]:
            if not isinstance(span, dict) or type(span.get("start")) is not int or type(span.get("end")) is not int:
                raise ValueError(f"Invalid annotation range for {bid}")
            if not end <= span["start"] < span["end"] or not isinstance(span.get("annotations"), list):
                raise ValueError(f"Overlapping or invalid annotation range for {bid}")
            annotation_classes(span["annotations"])
            end = span["end"]
    images = data.get("images", {})
    if not isinstance(images, dict):
        raise ValueError("Metadata image entries must be an object")
    for bid, entry in images.items():
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise ValueError(f"Invalid image metadata hash for {bid}")
        if not isinstance(entry.get("width"), (int, float)) or not 0 < entry["width"] < 1000000:
            raise ValueError(f"Invalid image width for {bid}")
        if type(entry.get("page_width")) is not bool or entry.get("alignment") not in {"left", "center", "right"}:
            raise ValueError(f"Invalid image layout for {bid}")
    return data


def apply_code_metadata(pre, entry, soup):
    code = pre.find("code")
    if code is None:
        raise ValueError(f"Missing code element for {block_id(pre)}")
    text = code.get_text()
    if text_hash(text) != entry["sha256"]:
        raise ValueError(f"Metadata does not match exported code {block_id(pre)}. Refresh the snapshot for this export.")
    if any(span["end"] > len(text) for span in entry["ranges"]):
        raise ValueError(f"Annotation extends beyond code {block_id(pre)}")
    pre["class"] = [c for c in pre.get("class", []) if c != "code-wrap"]
    if entry["wrap"]:
        pre["class"].append("code-wrap")
    if entry["ranges"]:
        code.clear()
        position = 0
        for span in entry["ranges"]:
            code.append(text[position:span["start"]])
            mark = soup.new_tag("span", attrs={"class": ["notion-code-annotation"] + annotation_classes(span["annotations"])})
            mark.string = text[span["start"]:span["end"]]
            code.append(mark)
            position = span["end"]
        code.append(text[position:])
    return len(entry["ranges"])


def snapshot_records(exported, records, site, images=None):
    code = {}
    for bid, text in sorted(exported.items()):
        record = records.get(bid)
        if not record or record.get("type") != "code":
            raise ValueError(f"Public Notion code block unavailable: {bid}")
        runs = record.get("properties", {}).get("title", [])
        live_text = "".join(run[0] for run in runs)
        if live_text != text:
            raise ValueError(f"Live code differs from the HTML export: {bid}. Create a fresh export before refreshing its metadata.")
        ranges, offset = [], 0
        for run in runs:
            end = offset + len(run[0])
            annotations = run[1] if len(run) > 1 else []
            if annotations and end > offset:
                annotation_classes(annotations)
                ranges.append({"start": offset, "end": end, "annotations": annotations})
            offset = end
        code[bid] = {"sha256": text_hash(text), "wrap": bool(record.get("format", {}).get("code_wrap")), "ranges": ranges}
    image_formats = {}
    for bid, exported_image in sorted((images or {}).items()):
        record = records.get(bid)
        if not record or record.get("type") != "image":
            raise ValueError(f"Public Notion image block unavailable: {bid}")
        fmt = record.get("format", {})
        width = fmt.get("block_width", exported_image["width"])
        if abs(width - exported_image["width"]) > .01:
            raise ValueError(f"Live image width differs from the export: {bid}. Create a fresh export before refreshing metadata.")
        if fmt.get("block_full_width"):
            raise ValueError(f"Full-bleed image layout is not supported yet: {bid}")
        image_formats[bid] = {"sha256": exported_image["sha256"], "width": width, "page_width": bool(fmt.get("block_page_width")), "alignment": fmt.get("block_alignment", "center")}
    return {"generator": GENERATOR, "schema": SCHEMA, "source": site, "offset_unit": "unicode-code-points", "code": code, "images": image_formats}


def export_images(source):
    images = {}
    for path in sorted(source.rglob("*.html")):
        if any(part.startswith(".") for part in path.relative_to(source).parts):
            continue
        if not path.resolve().is_relative_to(source):
            raise ValueError("An export symlink points outside the source directory")
        soup = BeautifulSoup(path.read_text(encoding="utf-8-sig"), "html.parser")
        for figure in soup.select("figure.image[id]"):
            image = figure.find("img")
            if image is None:
                continue
            src = urlsplit(image.get("src", ""))
            match = re.search(r"(?:^|;)\s*width\s*:\s*([\d.]+)px", image.get("style", ""))
            if src.scheme or src.netloc or not match:
                continue
            asset = (path.parent / unquote(src.path)).resolve()
            if not asset.is_relative_to(source) or not asset.is_file():
                raise ValueError(f"Missing local image: {src.path}")
            images[figure["id"]] = {"sha256": hashlib.sha256(asset.read_bytes()).hexdigest(), "width": float(match.group(1))}
    return images


def export_code(source):
    exported, spaces = {}, set()
    for path in sorted(source.rglob("*.html")):
        if any(part.startswith(".") for part in path.relative_to(source).parts):
            continue
        if not path.resolve().is_relative_to(source):
            raise ValueError("An export symlink points outside the source directory")
        soup = BeautifulSoup(path.read_text(encoding="utf-8-sig"), "html.parser")
        spaces.update(e["data-notion-space-id"] for e in soup.select("[data-notion-space-id]"))
        for pre in soup.select("pre.code"):
            bid = block_id(pre)
            if not bid or not pre.find("code"):
                raise ValueError(f"Code block without a Notion ID in {path}")
            text = pre.find("code").get_text()
            if bid in exported and exported[bid] != text:
                raise ValueError(f"Conflicting exported copies of code block {bid}")
            exported[bid] = text
    if len(spaces) > 1:
        raise ValueError("Export contains several Notion spaces; snapshot one site at a time")
    return exported, next(iter(spaces), None)


def fetch_records(ids, site, space_id=None):
    import requests  # Only the snapshot command needs an HTTP client.

    # Notion can return a deterministic MemcachedCrossCellError for a batch,
    # even when its individual public records are readable. Try the public
    # canonical origin, then bisect failed batches instead of repeating the
    # same failing request. Access-denied responses are never routed around.
    origins = [site.rstrip("/")]
    canonical = "https://www.notion.so"
    if origins[0] != canonical:
        origins.append(canonical)

    with requests.Session() as session:
        def request(origin, requests_body):
            for attempt in range(3):
                try:
                    response = session.post(origin + "/api/v3/getRecordValues", json={"requests": requests_body}, timeout=30)
                except requests.RequestException as exc:
                    failure = exc
                else:
                    if response.status_code >= 500:
                        return None
                    if response.status_code >= 400:
                        raise ValueError(f"Public Notion API returned HTTP {response.status_code}")
                    try:
                        return response.json()
                    except ValueError as exc:
                        failure = exc
                if attempt == 2:
                    raise ValueError(f"Cannot read public Notion metadata: {failure}") from failure
                time.sleep(attempt + 1)

        def read_batch(batch):
            requests_body = [{"id": bid, "table": "block", **({"spaceId": space_id} if space_id else {})} for bid in batch]
            for origin in origins:
                payload = request(origin, requests_body)
                if payload is None:
                    continue
                results = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(results, list) or len(results) != len(batch):
                    raise ValueError("Public Notion API returned an incomplete response")
                found = {}
                for bid, result in zip(batch, results):
                    value = result.get("value") if isinstance(result, dict) else None
                    # Some response versions wrap a record in value/role pairs.
                    while isinstance(value, dict) and "id" not in value and isinstance(value.get("value"), dict):
                        value = value["value"]
                    if not isinstance(value, dict) or value.get("id") != bid:
                        raise ValueError(f"Public block unavailable: {bid}")
                    found[bid] = value
                return found
            if len(batch) > 1:
                middle = len(batch) // 2
                return read_batch(batch[:middle]) | read_batch(batch[middle:])
            raise ValueError(f"Public Notion API could not read block {batch[0]} (server error). The previous snapshot has not been replaced.")

        records = {}
        for offset in range(0, len(ids), 50):
            records.update(read_batch(ids[offset:offset + 50]))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Extracted HTML export")
    parser.add_argument("output", help="Metadata JSON snapshot (outside the export)")
    parser.add_argument("--notion-site", required=True, help="Public Notion site URL")
    args = parser.parse_args()
    site = args.notion_site.rstrip("/")
    u = urlsplit(site)
    if u.scheme != "https" or not u.hostname or not (u.hostname.endswith(".notion.site") or u.hostname in {"www.notion.so", "www.notion.com", "notion.so", "notion.com"}) or u.path or u.query or u.fragment or u.username or u.port:
        parser.error("--notion-site must be an HTTPS public Notion origin, such as https://example.notion.site")
    try:
        source = Path(args.source).expanduser().resolve()
        output = Path(args.output).expanduser()
        if not source.is_dir() or output.is_symlink() or output.resolve().is_relative_to(source):
            raise ValueError("Use an extracted export and a real output file outside it")
        if output.exists():
            read_metadata(output)  # Never replace an unrelated JSON file.
        exported, space_id = export_code(source)
        images = export_images(source)
        records = fetch_records(sorted(exported.keys() | images.keys()), site, space_id)
        data = snapshot_records(exported, records, site, images)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, prefix=".notion-metadata-", delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.close()
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    annotations = sum(len(entry["ranges"]) for entry in data["code"].values())
    print(f"Saved {len(data['code'])} code wrapping settings, {annotations} annotation ranges, and {len(data['images'])} image layouts to {output}")


if __name__ == "__main__":
    main()
