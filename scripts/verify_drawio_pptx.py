#!/usr/bin/env python3
"""Check source immutability, native text and PPTX objects; not visual fidelity.

One source page per slide, source order, one text-bearing native object per label.
For adapters splitting/combining labels, extend verification with an explicit map;
do not remove missing-label findings to make a conversion pass.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import posixpath
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_pptx_compatibility import check as check_compatibility

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def normalize(text):
    return " ".join(text.split())


def native_label(shape):
    return normalize(" ".join(
        "".join((node.text or "") if node.tag == f"{{{NS['a']}}}t" else " "
                for node in para.iter() if node.tag in {f"{{{NS['a']}}}t", f"{{{NS['a']}}}br", f"{{{NS['a']}}}tab"})
        for para in shape.findall("p:txBody/a:p", NS)
    ))


def verify(manifest, pptx):
    errors, warnings, results = [], [], []
    compatibility = check_compatibility(pptx)
    if not compatibility["checks_passed"]:
        return {"technical_checks_passed": False, "visual_review_required": True,
                "source_sha256": manifest["source_sha256"],
                "pptx_sha256": compatibility["pptx_sha256"],
                "compatibility": compatibility,
                "errors": compatibility["errors"], "warnings": warnings, "slides": results}
    source = Path(manifest["source"])
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["source_sha256"]:
        errors.append("Source Draw.io changed since inventory; create a fresh inventory and reconvert")
    with zipfile.ZipFile(pptx) as archive:
        bad = archive.testzip()
        if bad:
            errors.append(f"ZIP integrity failure: {bad}")
        deck = ET.fromstring(archive.read("ppt/presentation.xml"))
        rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels if r.get("TargetMode") != "External"}
        slide_paths = []
        for ref in deck.findall("p:sldIdLst/p:sldId", NS):
            target = targets[ref.get(f"{{{NS['r']}}}id")]
            slide_paths.append(target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("ppt", target)))
        if len(slide_paths) != len(manifest["pages"]):
            errors.append(f"Slide count {len(slide_paths)} does not match source pages {len(manifest['pages'])}")
        for index, (page, filename) in enumerate(zip(manifest["pages"], slide_paths), 1):
            root = ET.fromstring(archive.read(filename))
            shapes = root.findall(".//p:sp", NS)
            pictures = root.findall(".//p:pic", NS)
            connectors = root.findall(".//p:cxnSp", NS)
            native_text = [native_label(shape) for shape in shapes]
            actual = Counter(text for text in native_text if text)
            expected = Counter(normalize(c["text"]) for c in page["cells"]
                               if c["visible"] and c["kind"] != "container" and normalize(c["text"]))
            missing = expected - actual
            if missing:
                errors.append(f"Slide {index}: missing native labels (including multiplicity): {dict(missing)}")
            for lock in root.iter():
                if any(lock.get(key) in {"1", "true"} for key in ("noTextEdit", "noSelect")):
                    errors.append(f"Slide {index}: text editing or selection lock present")
                    break
            source_images = sum(c["visible"] and c["kind"] == "image" for c in page["cells"])
            source_structure = sum(c["visible"] and (c["kind"] == "edge" or (c["kind"] == "vertex" and not c["text"])) for c in page["cells"])
            if source_images != len(pictures):
                warnings.append(f"Slide {index}: source image cells={source_images}, PPT pictures={len(pictures)}; reconcile in object map")
            native_nontext = sum(not text for text in native_text) + len(connectors)
            if source_structure and not native_nontext:
                errors.append(f"Slide {index}: source structures exist but no non-text native shapes or connectors found")
            if pictures and not shapes and not connectors and (expected or source_structure):
                errors.append(f"Slide {index}: diagram appears flattened to pictures")
            results.append({"slide": index, "native_text_objects": sum(actual.values()),
                            "native_nontext_objects": native_nontext, "picture_objects": len(pictures),
                            "missing_labels": dict(missing)})
    return {"technical_checks_passed": not errors, "visual_review_required": True,
            "compatibility": compatibility,
            "source_sha256": manifest["source_sha256"],
            "pptx_sha256": hashlib.sha256(Path(pptx).read_bytes()).hexdigest(),
            "errors": errors, "warnings": warnings, "slides": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.inventory.read_text(encoding="utf-8"))
    if args.output.resolve() in {args.inventory.resolve(), args.pptx.resolve(), Path(manifest["source"]).resolve()}:
        parser.error("Report must not overwrite an input")
    result = verify(manifest, args.pptx)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["technical_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
