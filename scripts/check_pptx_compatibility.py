#!/usr/bin/env python3
"""Read-only checks for this skill's conservative PowerPoint/WPS export profile.

Not a full OOXML schema validator, image/font decoder, or application test.
Only the optional JSON report is written; the input PPTX is never modified.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import posixpath
import struct
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCREL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STRICTREL = "http://purl.oclc.org/ooxml/officeDocument/relationships"
FONT_MIME = "application/x-fontdata"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def source_for_rels(name):
    if name == "_rels/.rels":
        return ""
    directory, filename = posixpath.split(name)
    if posixpath.basename(directory) != "_rels" or not filename.endswith(".rels"):
        raise ValueError("Invalid relationship part path: " + name)
    return posixpath.join(posixpath.dirname(directory), filename[:-5])


def internal_target(source, target):
    url = urlsplit(target)
    if url.scheme or url.netloc or url.query or url.fragment or not url.path:
        raise ValueError("Invalid internal relationship target: " + target)
    decoded = unquote(url.path)
    if "\\" in decoded:
        raise ValueError("Backslash in internal target: " + target)
    name = posixpath.normpath(posixpath.join(posixpath.dirname(source), decoded)).lstrip("/")
    if name in {"", ".", ".."} or name.startswith("../"):
        raise ValueError("Relationship escapes package: " + target)
    return name


def eot_header_errors(data):
    if len(data) < 82:
        return ["EOT header is truncated"]
    total, font_size, version = struct.unpack_from("<III", data)
    issues = []
    if total != len(data):
        issues.append("EOT total size does not match payload")
    if not 0 < font_size <= len(data) - 82:
        issues.append("EOT FontDataSize is out of bounds")
    if version not in {0x00010000, 0x00020001, 0x00020002}:
        issues.append("Unsupported EOT header version")
    if struct.unpack_from("<H", data, 34)[0] != 0x504C:
        issues.append("Missing EOT magic; do not relabel raw TTF/OTF as EOT")
    return issues


def inspect_archive(archive, report):
    errors = report["errors"]
    names = [i.filename for i in archive.infolist() if not i.is_dir()]
    members = set(names)
    for name, count in Counter(names).items():
        if count > 1:
            errors.append("Duplicate ZIP member: " + name)
    bad = archive.testzip()
    if bad:
        errors.append("ZIP CRC failure: " + bad)
    required = {"[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml",
                "ppt/_rels/presentation.xml.rels"}
    errors.extend("Missing required part: " + n for n in sorted(required - members))
    trees = {}
    for name in names:
        if name.endswith((".xml", ".rels")):
            try:
                data = archive.read(name)
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                    raise ValueError("DTD/entity declarations are not allowed in this profile")
                trees[name] = ET.fromstring(data)
            except (ET.ParseError, ValueError) as exc:
                errors.append(f"{name}: {exc}")
    report["xml_parts_parsed"] = len(trees)
    defaults, overrides = {}, {}
    content_types = trees.get("[Content_Types].xml")
    if content_types is None:
        return
    if content_types.tag != f"{{{CT}}}Types":
        errors.append("Invalid content-types root namespace")
    for item in content_types:
        if item.tag == f"{{{CT}}}Default":
            key = item.get("Extension", "").lower()
            table = defaults
        elif item.tag == f"{{{CT}}}Override":
            key = unquote(item.get("PartName", "")).lstrip("/")
            table = overrides
            if key not in members:
                errors.append("Content-type override points to missing part: " + key)
        else:
            errors.append("Invalid content-type entry")
            continue
        if not key or not item.get("ContentType") or key in table:
            errors.append("Empty/duplicate content-type declaration: " + key)
        table[key] = item.get("ContentType", "")

    def mime(name):
        return overrides.get(name, defaults.get(name.rsplit(".", 1)[-1].lower(), ""))

    for name in names:
        if name != "[Content_Types].xml" and not mime(name):
            errors.append("No content-type declaration: " + name)
    rels_by_source, font_parts, image_parts = {}, set(), set()
    for name, tree in trees.items():
        if not name.endswith(".rels"):
            continue
        try:
            source = source_for_rels(name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if source and source not in members:
            errors.append("Relationship source part is missing: " + source)
        if tree.tag != f"{{{REL}}}Relationships":
            errors.append("Invalid relationships root namespace: " + name)
        ids = {}
        for rel in tree:
            rid, target, kind = rel.get("Id"), rel.get("Target"), rel.get("Type", "")
            if rel.tag != f"{{{REL}}}Relationship" or not rid or not target or not kind:
                errors.append("Incomplete relationship: " + name)
                continue
            if rid in ids:
                errors.append(f"Duplicate relationship ID: {name} {rid}")
            ids[rid] = rel
            resource_kind = kind.rsplit("/", 1)[-1]
            if rel.get("TargetMode") == "External":
                if resource_kind in {"image", "font"}:
                    errors.append("External image/font dependency: " + target)
                continue
            if rel.get("TargetMode") not in {None, "Internal"}:
                errors.append("Invalid relationship TargetMode: " + name)
            try:
                resolved = internal_target(source, target)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
                continue
            if resolved not in members:
                errors.append(f"Missing relationship target: {name} -> {resolved}")
            if resource_kind == "font":
                font_parts.add(resolved)
            if resource_kind == "image":
                image_parts.add(resolved)
        rels_by_source[source] = ids
    for name, tree in trees.items():
        if name.endswith(".rels"):
            continue
        for element in tree.iter():
            for attribute, rid in element.attrib.items():
                if attribute.startswith((f"{{{DOCREL}}}", f"{{{STRICTREL}}}")):
                    if rid not in rels_by_source.get(name, {}):
                        errors.append(f"Unresolved relationship reference: {name} {rid}")
    font_parts.update(n for n in names if n.startswith("ppt/fonts/"))
    image_parts.update(n for n in names if mime(n).startswith("image/") or n.lower().endswith(".svg"))
    for name in sorted(font_parts & members):
        data = archive.read(name)
        if mime(name) != FONT_MIME:
            errors.append(f"{name}: expected EOT MIME {FONT_MIME}, got {mime(name)}")
        errors.extend(f"{name}: {issue}" for issue in eot_header_errors(data))
        report["font_parts"].append({"part": name, "content_type": mime(name)})
    for name in sorted(image_parts & members):
        content_type, data = mime(name), archive.read(name)
        record = {"part": name, "content_type": content_type}
        if content_type == "image/png":
            if len(data) < 33 or not data.startswith(PNG_MAGIC) or data[8:16] != b"\x00\x00\x00\rIHDR":
                errors.append("Invalid PNG header: " + name)
            else:
                width, height = struct.unpack_from(">II", data, 16)
                record.update(width=width, height=height)
                if not width or not height or (width == 1 and height == 1):
                    errors.append("Empty/1x1 placeholder image: " + name)
        elif content_type == "image/jpeg":
            if not data.startswith(b"\xff\xd8\xff"):
                errors.append("Invalid JPEG header: " + name)
        else:
            errors.append(f"{name}: image type {content_type!r} is outside the PNG/JPEG compatibility profile")
        if name.lower().endswith(".svg"):
            errors.append("SVG part retained; rasterize actual asset before export: " + name)
        report["image_parts"].append(record)


def check(pptx):
    report = {"profile": "powerpoint-wps-v1", "checks_passed": False,
              "pptx_sha256": None, "xml_parts_parsed": 0,
              "font_parts": [], "image_parts": [], "errors": [],
              "full_schema_validation": "not-performed",
              "application_tests": {"microsoft_powerpoint": "not-tested", "wps": "not-tested"},
              "visual_review_required": True}
    try:
        report["pptx_sha256"] = hashlib.sha256(Path(pptx).read_bytes()).hexdigest()
        with zipfile.ZipFile(pptx) as archive:
            inspect_archive(archive, report)
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError, ValueError, NotImplementedError) as exc:
        report["errors"].append(f"Cannot validate PPTX package: {exc}")
    report["errors"] = list(dict.fromkeys(report["errors"]))
    report["checks_passed"] = not report["errors"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.pptx.resolve():
        parser.error("Report must not overwrite the PPTX")
    result = check(args.pptx)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
