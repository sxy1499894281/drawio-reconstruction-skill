#!/usr/bin/env python3
"""Inventory Draw.io XML for agent-assisted PPTX conversion; never redraw or convert.

The JSON retains raw cell/style/geometry data. Feature flags are review prompts,
not a claim that arbitrary Draw.io constructs are supported by a fixed converter.
"""
import argparse
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import urllib.parse
import xml.etree.ElementTree as ET
import zlib


class LabelText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def boundary(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")
        elif tag in {"div", "p", "li", "tr"}:
            self.boundary()

    def handle_endtag(self, tag):
        if tag in {"div", "p", "li", "tr"}:
            self.boundary()

    def handle_data(self, value):
        self.parts.append(value)


def label_text(value, html=False):
    if not html:
        return value.strip()
    parser = LabelText()
    parser.feed(value)
    return "".join(parser.parts).strip()


def style_map(raw):
    return dict(part.split("=", 1) for part in raw.split(";") if "=" in part)


def models(raw):
    root = ET.fromstring(raw)
    if root.tag == "mxGraphModel":
        return [("1", "Page 1", root)]
    diagrams = [root] if root.tag == "diagram" else root.findall("diagram")
    result = []
    for index, diagram in enumerate(diagrams, 1):
        model = diagram.find("mxGraphModel")
        if model is None:
            payload = (diagram.text or "").strip()
            if not payload:
                raise ValueError(f"Page {index} has no graph data")
            if not payload.startswith("<"):
                payload = urllib.parse.unquote(
                    zlib.decompress(base64.b64decode(payload), -15).decode("utf-8")
                )
            model = ET.fromstring(payload)
        if model.tag != "mxGraphModel":
            raise ValueError(f"Page {index} is not an mxGraphModel")
        result.append((diagram.get("id", str(index)), diagram.get("name", f"Page {index}"), model))
    if not result:
        raise ValueError("No Draw.io pages found")
    return result


def inventory(source):
    source = Path(source).resolve()
    raw = source.read_bytes()
    pages = []
    for page_id, page_name, model in models(raw):
        parent_map = {child: parent for parent in model.iter() for child in parent}
        cells = model.findall(".//mxCell")
        records = []
        for order, cell in enumerate(cells):
            wrapper = parent_map.get(cell)
            wrapped = wrapper is not None and wrapper.tag not in {"root", "mxGraphModel"}
            attrs = dict(cell.attrib)
            if wrapped:
                attrs.setdefault("id", wrapper.get("id", ""))
                attrs.setdefault("value", wrapper.get("label", ""))
            if not attrs.get("id"):
                raise ValueError(f"Page {page_name}: cell without an id")
            style = style_map(attrs.get("style", ""))
            value = attrs.get("value", "")
            geo = cell.find("mxGeometry")
            flags = []
            if wrapped:
                flags.append("wrapped-cell-or-placeholder")
            if attrs.get("source") or attrs.get("target"):
                flags.append("connected-edge-resolve-terminals-and-route")
            if geo is not None and geo.get("relative") == "1":
                flags.append("relative-geometry")
            if any(key in style for key in ("rotation", "flipH", "flipV")):
                flags.append("transform")
            if any(key in style for key in ("startArrow", "endArrow", "curved", "edgeStyle")):
                flags.append("edge-style-and-arrowheads")
            if style.get("shape") not in {None, "image", "rectangle", "ellipse"}:
                flags.append("special-shape")
            if style.get("image") and not style["image"].startswith("data:"):
                flags.append("external-image-requires-explicit-resolution")
            if "<" in value and style.get("html") == "1":
                flags.append("rich-text-preserve-runs")
            if attrs.get("placeholders") == "1" or (wrapped and wrapper.get("placeholders") == "1"):
                flags.append("dynamic-label-resolve-before-verification")
            kind = "edge" if attrs.get("edge") == "1" else "image" if style.get("image") else "vertex" if attrs.get("vertex") == "1" else "container"
            records.append({
                "id": attrs["id"], "order": order, "kind": kind,
                "attributes": attrs, "style": style,
                "geometry": dict(geo.attrib) if geo is not None else None,
                "geometry_xml": ET.tostring(geo, encoding="unicode") if geo is not None else None,
                "value": value, "text": label_text(value, style.get("html") == "1"),
                "wrapper_attributes": dict(wrapper.attrib) if wrapped else None,
                "review_features": flags,
            })
        by_id = {item["id"]: item for item in records}
        if len(by_id) != len(records):
            raise ValueError(f"Page {page_name}: duplicate cell ids")
        for item in records:
            visible, cursor, visited = True, item, set()
            while cursor:
                if cursor["id"] in visited:
                    raise ValueError(f"Page {page_name}: parent cycle at {cursor['id']}")
                visited.add(cursor["id"])
                if cursor["attributes"].get("visible") == "0":
                    visible = False
                if cursor is not item and cursor["attributes"].get("collapsed") == "1":
                    visible = False
                cursor = by_id.get(cursor["attributes"].get("parent"))
            item["visible"] = visible
            parent = by_id.get(item["attributes"].get("parent"))
            if parent and parent["kind"] in {"vertex", "edge", "image"}:
                item["review_features"].append("nested-parent-resolve-coordinates")
        pages.append({"id": page_id, "name": page_name, "model_attributes": dict(model.attrib), "cells": records})
    return {"schema_version": 1, "source": str(source), "source_sha256": hashlib.sha256(raw).hexdigest(), "pages": pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() == args.source.resolve():
        parser.error("Inventory output must not overwrite the source")
    result = inventory(args.source)
    # Exclusive creation also protects existing user files and symlink targets.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"pages": len(result["pages"]), "inventory": str(args.output.resolve()), "summary": [
        {"name": page["name"], "visible_objects": sum(c["visible"] and c["kind"] != "container" for c in page["cells"]),
         "review_features": sorted({f for c in page["cells"] if c["visible"] for f in c["review_features"]})}
        for page in result["pages"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
