import argparse
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"


def load_module(name, relative_path):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SkillContractTests(unittest.TestCase):
    def test_skill_stays_concise_and_has_supported_frontmatter(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 500)
        frontmatter = text.split("---", 2)[1]
        keys = {
            line.split(":", 1)[0].strip()
            for line in frontmatter.splitlines()
            if ":" in line
        }
        self.assertEqual(keys, {"name", "description"})

    def test_production_and_review_are_separate_fresh_agents(self):
        text = SKILL.read_text(encoding="utf-8")
        required = (
            "## Mandatory Role Separation",
            "A producer must never issue `PASS`",
            "fresh Icon Review Agent",
            "start a fresh Reconstruction Producer",
            "fresh Reconstruction Review Agent",
            "fresh Icon Repair Producer",
            "fresh **Reconstruction Repair Producer Agent**",
            "new fresh read-only Icon Reviewer instance",
            "new fresh read-only Reconstruction Reviewer instance",
            "globally unique across all rounds and phases",
            "Do not reuse any completed agent's cached response",
            "without a fixed round or time limit",
            "Never close, interrupt, replace, or abandon an active producer or reviewer merely because it is slow",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        for forbidden in (
            "return the defect list to the same worker",
            "goes back to the original worker",
            "producer performs the review",
            "producer may return `pass`",
        ):
            self.assertNotIn(forbidden, text.lower())

    def test_review_contract_binds_identity_version_and_hashes(self):
        text = SKILL.read_text(encoding="utf-8")
        for phrase in (
            "phase | artifact_version | producer_id | reviewer_id | verdict | fix_ids | artifact_sha256",
            "The read-only Reviewer returns a signed result",
            "The coordinator copies that result verbatim",
            "Reject a verdict whose version or hashes do not match",
            "from the **actual exported preview**",
            "Take both identifiers from the agent-launch metadata",
            "scripts/validate_review_result.py",
            "captures workspace hashes before and after read-only review",
            "must never rewrite, normalize, summarize, or create a substitute result",
        ):
            self.assertIn(phrase, text)

    def test_review_result_validator_rejects_self_review_and_incomplete_ledger(self):
        module = load_module("validate_review_result", "scripts/validate_review_result.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            icons = root / "icons.json"
            artifact = root / "preview.png"
            result = root / "review.txt"
            icons.write_text(json.dumps({"icons": [{"id": "a"}, {"id": "b"}]}), encoding="utf-8")
            artifact.write_bytes(b"preview")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            base = (
                "PASS\n"
                "producer_id: producer-actual\n"
                "reviewer_id: reviewer-actual\n"
                "artifact_version: recon-v2\n"
                f"artifact_sha256: preview={digest}\n"
                "icon_verdicts:\n"
                "- a: PASS - clean\n"
                "- b: PASS - clean\n"
                "non_icon_fixes: none\n"
            )
            result.write_text(base, encoding="utf-8")
            args = argparse.Namespace(
                result=result,
                icons=icons,
                producer_id="producer-actual",
                reviewer_id="reviewer-actual",
                artifact_version="recon-v2",
                artifact=[f"preview={artifact}"],
            )
            self.assertEqual(module.validate(args), [])

            multiline = base.replace(
                f"artifact_sha256: preview={digest}\n",
                f"artifact_sha256:\n- preview: {digest}\n",
            )
            result.write_text(multiline, encoding="utf-8")
            self.assertEqual(module.validate(args), [])

            result.write_text(base.replace("- b: PASS - clean\n", ""), encoding="utf-8")
            self.assertIn(
                "icon_verdicts ids/order do not exactly match icons.json",
                module.validate(args),
            )

            result.write_text(base, encoding="utf-8")
            args.reviewer_id = "producer-actual"
            self.assertIn("producer_id and reviewer_id must differ", module.validate(args))

    def test_icon_review_evidence_is_sharded_literal_and_exhaustive(self):
        text = SKILL.read_text(encoding="utf-8")
        for phrase in (
            "Never put every icon into one tall review image",
            "at most **8 icon rows**",
            "at most 2200 px high",
            "1 source pixel to 1 sheet pixel",
            "Never scale either panel down to fit a fixed cell",
            "source-context panel extending beyond the bbox with the bbox outlined",
            "one explicit verdict for every icon id",
            "exactly equal the manifest id set with no omissions or duplicates",
            "reject an empty ledger, a missing id, a duplicate id, an extra id",
            "one entry for every remaining icon id in exact manifest order",
            "Empty, missing, duplicate, extra, or reordered ids invalidate the whole verdict",
            "placement-review*.png",
            "outlined final-preview-context panel",
            "audit inventory, `<stem>_icons/icons.json`, and every `placement-review*.png` shard",
            "any primitive extending outside it is a blocking preflight failure",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_batch_manifest_supports_all_rasters_and_unique_safe_previews(self):
        module = load_module("batch_manifest", "scripts/batch_manifest.py")
        extensions = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            source_path = Path(source)
            for index, extension in enumerate(extensions):
                (source_path / f"image-{index}{extension}").write_bytes(b"test")
            (source_path / "same.jpg").write_bytes(b"jpg")
            (source_path / "same.png").write_bytes(b"png")
            manifest = module.make_manifest(source_path, Path(output))
        self.assertEqual(len(manifest["entries"]), len(extensions) + 2)
        previews = [Path(entry["preview"]) for entry in manifest["entries"]]
        self.assertEqual(len(previews), len(set(previews)))
        for entry in manifest["entries"]:
            self.assertNotEqual(Path(entry["image"]), Path(entry["preview"]))
            self.assertTrue(Path(entry["preview"]).name.endswith("_preview.png"))

    def test_export_helper_uses_windows_override_and_safe_default_name(self):
        module = load_module("export_drawio", "scripts/export_drawio.py")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "draw.io.exe"
            executable.write_bytes(b"test")
            with mock.patch.dict(os.environ, {"DRAWIO_PATH": str(executable)}):
                self.assertEqual(module.find_drawio(), str(executable))
        self.assertEqual(module.default_output_path(Path("case.drawio")), Path("case_preview.png"))
        helper = (ROOT / "scripts" / "export_drawio.py").read_text(encoding="utf-8")
        self.assertIn('encoding="utf-8"', helper)
        self.assertIn('errors="replace"', helper)

    def test_checker_is_generic_and_rejects_raw_raster_semicolon(self):
        module = load_module("check_drawio", "scripts/check_drawio.py")
        raw = ET.fromstring(
            '<mxCell id="icon" style="shape=image;image=data:image/png;base64,AAAA;" />'
        )
        encoded = ET.fromstring(
            '<mxCell id="icon" style="shape=image;image=data:image/png%3Bbase64,AAAA;" />'
        )
        self.assertTrue(module._image_encoding_failures({"icon": raw}))
        self.assertFalse(module._image_encoding_failures({"icon": encoded}))
        checker = (ROOT / "scripts" / "check_drawio.py").read_text(encoding="utf-8")
        for case_id in ("user_panel", "workflow_box", "ev_icon", "core_icon", "out_icon"):
            self.assertNotIn(case_id, checker)

    def test_checker_rejects_desktop_incompatible_file_prefixes(self):
        module = load_module("check_drawio_header", "scripts/check_drawio.py")
        self.assertFalse(module._file_header_failures(b'<mxfile><diagram /></mxfile>'))
        self.assertFalse(
            module._file_header_failures(
                b'<?xml version="1.0" encoding="UTF-8"?><mxfile><diagram /></mxfile>'
            )
        )
        self.assertTrue(module._file_header_failures(b'\r\n<mxfile><diagram /></mxfile>'))
        self.assertTrue(
            module._file_header_failures(b'\xef\xbb\xbf<mxfile><diagram /></mxfile>')
        )

    def test_checker_ignores_edge_label_offset_but_keeps_route_points(self):
        module = load_module("check_drawio_routes", "scripts/check_drawio.py")
        geometry = ET.fromstring(
            '<mxGeometry relative="1" as="geometry">'
            '<mxPoint x="10" y="20" as="sourcePoint" />'
            '<mxPoint x="30" y="40" as="targetPoint" />'
            '<Array as="points"><mxPoint x="50" y="60" /></Array>'
            '<mxPoint x="-120" y="-80" as="offset" />'
            '</mxGeometry>'
        )
        points = list(module._edge_route_points(geometry))
        self.assertEqual(
            [(point.attrib.get("as"), point.attrib["x"], point.attrib["y"]) for point in points],
            [("sourcePoint", "10", "20"), ("targetPoint", "30", "40"), (None, "50", "60")],
        )

    def test_python_helpers_compile_and_examples_pass(self):
        for path in (ROOT / "scripts").glob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts" / "batch_verify.py"),
                str(ROOT / "examples"),
                "--no-export",
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bilingual_readmes_match_preview_and_review_contract(self):
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for text in (english, chinese):
            self.assertIn("<stem>_preview.png", text)
            self.assertNotIn("<stem>.png", text)
            self.assertIn("Icon Producer", text)
            self.assertIn("Icon Reviewer", text)
            self.assertIn("placement-review*.png", text)


class PptxHelperTests(unittest.TestCase):
    def setUp(self):
        self.inspect = load_module("inspect_pptx_source", "scripts/inspect_drawio_for_pptx.py")
        self.verify = load_module("verify_pptx_native", "scripts/verify_drawio_pptx.py")

    def source(self, directory, labels=("First", "Second")):
        root = ET.Element("mxGraphModel", pageWidth="640", pageHeight="360")
        cells = ET.SubElement(root, "root")
        ET.SubElement(cells, "mxCell", id="0")
        ET.SubElement(cells, "mxCell", id="1", parent="0")
        panel = ET.SubElement(cells, "mxCell", id="panel", parent="1", vertex="1", style="rounded=1;fillColor=#FFFFFF;")
        ET.SubElement(panel, "mxGeometry", x="10", y="10", width="620", height="340")
        for index, label in enumerate(labels):
            cell = ET.SubElement(cells, "mxCell", id=f"text-{index}", parent="1", vertex="1", value=label)
            ET.SubElement(cell, "mxGeometry", x="20", y=str(30+index*30), width="300", height="30")
        path = Path(directory) / "source.drawio"
        path.write_bytes(ET.tostring(root))
        return path

    def package(self, directory, slide_labels, *, pictures_only=False, locked=False):
        # Minimal OOXML parts for structural-check unit tests, not a rendered deck.
        ns = self.verify.NS
        p, a, r = (ns[key] for key in ("p", "a", "r"))
        pres = ET.Element(f"{{{p}}}presentation")
        ids = ET.SubElement(pres, f"{{{p}}}sldIdLst")
        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        rels = ET.Element(f"{{{rel_ns}}}Relationships")
        path = Path(directory) / "test.pptx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '</Types>')
            archive.writestr("_rels/.rels",
                f'<Relationships xmlns="{rel_ns}"><Relationship Id="rId1" '
                f'Type="{r}/officeDocument" Target="ppt/presentation.xml"/></Relationships>')
            for index, labels in enumerate(slide_labels, 1):
                ET.SubElement(ids, f"{{{p}}}sldId", {f"{{{r}}}id": f"rId{index}"})
                ET.SubElement(rels, f"{{{rel_ns}}}Relationship", Id=f"rId{index}",
                              Type=f"{r}/slide", Target=f"slides/slide{index}.xml")
                slide = ET.Element(f"{{{p}}}sld")
                tree = ET.SubElement(slide, f"{{{p}}}spTree")
                if pictures_only:
                    ET.SubElement(tree, f"{{{p}}}pic")
                else:
                    ET.SubElement(tree, f"{{{p}}}sp")  # Native panel.
                    for label in labels:
                        shape = ET.SubElement(tree, f"{{{p}}}sp")
                        body = ET.SubElement(shape, f"{{{p}}}txBody")
                        para = ET.SubElement(body, f"{{{a}}}p")
                        for part_index, part in enumerate(label.split("\n")):
                            if part_index:
                                ET.SubElement(para, f"{{{a}}}br")
                            run = ET.SubElement(para, f"{{{a}}}r")
                            ET.SubElement(run, f"{{{a}}}t").text = part
                        if locked:
                            ET.SubElement(shape, f"{{{a}}}spLocks", noTextEdit="1")
                archive.writestr(f"ppt/slides/slide{index}.xml", ET.tostring(slide))
            archive.writestr("ppt/presentation.xml", ET.tostring(pres))
            archive.writestr("ppt/_rels/presentation.xml.rels", ET.tostring(rels))
        return path

    def test_inventory_preserves_html_raw_style_and_plain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory, ("x < y",))
            graph = ET.fromstring(source.read_bytes())
            cell = graph.find(".//mxCell[@id='text-0']")
            cell.set("style", "html=1;fontSize=18;")
            cell.set("value", '<div><b>中文 &amp; A</b><br/>B</div><div>C</div>')
            source.write_bytes(ET.tostring(graph))
            item = self.inspect.inventory(source)["pages"][0]["cells"][-1]
            self.assertEqual(item["text"], "中文 & A\nB\nC")
            self.assertEqual(item["value"], cell.get("value"))
            self.assertEqual(item["geometry"]["x"], "20")
            self.assertEqual(self.inspect.label_text("x < y", False), "x < y")

    def test_inventory_compressed_multpage_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory, ("中文标签",))
            raw = source.read_bytes()
            compressor = zlib.compressobj(wbits=-15)
            encoded = urllib.parse.quote(raw.decode()).encode()
            compressed = base64.b64encode(compressor.compress(encoded)+compressor.flush()).decode()
            mxfile = ET.Element("mxfile")
            first = ET.SubElement(mxfile, "diagram", id="first", name="原页")
            first.append(ET.fromstring(raw))
            ET.SubElement(mxfile, "diagram", id="second", name="压缩页").text = compressed
            source.write_bytes(ET.tostring(mxfile))
            result = self.inspect.inventory(source)
            self.assertEqual([p["name"] for p in result["pages"]], ["原页", "压缩页"])
            self.assertEqual(result["pages"][1]["cells"][-1]["text"], "中文标签")

    def test_inventory_wrappers_hidden_parents_and_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            graph = ET.fromstring(source.read_bytes())
            cells = graph.find("root")
            panel = cells.find("mxCell[@id='panel']")
            panel.set("visible", "0")
            wrapper = ET.SubElement(cells, "object", id="wrapped", label="Wrapped")
            ET.SubElement(wrapper, "mxCell", vertex="1", parent="panel")
            source.write_bytes(ET.tostring(graph))
            item = self.inspect.inventory(source)["pages"][0]["cells"][-1]
            self.assertEqual(item["id"], "wrapped")
            self.assertEqual(item["text"], "Wrapped")
            self.assertFalse(item["visible"])
            panel.set("parent", "wrapped")
            source.write_bytes(ET.tostring(graph))
            with self.assertRaisesRegex(ValueError, "parent cycle"):
                self.inspect.inventory(source)

    def test_cli_refuses_overwriting_input_or_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            before = source.read_bytes()
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/inspect_drawio_for_pptx.py"), str(source), "--output", str(source)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_bytes(), before)
            output = Path(directory) / "inventory.json"
            output.write_text("KEEP")
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/inspect_drawio_for_pptx.py"), str(source), "--output", str(output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), "KEEP")

    def test_verifier_accepts_native_text_and_line_breaks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory, ("Line one\nLine two",))
            result = self.verify.verify(self.inspect.inventory(source), self.package(directory, [["Line one\nLine two"]]))
            self.assertTrue(result["technical_checks_passed"], result)
            self.assertTrue(result["visual_review_required"])

    def test_verifier_rejects_missing_repeated_labels_and_flattening(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory, ("Same", "Same"))
            data = self.inspect.inventory(source)
            result = self.verify.verify(data, self.package(directory, [["Same"]]))
            self.assertFalse(result["technical_checks_passed"])
            self.assertEqual(result["slides"][0]["missing_labels"], {"Same": 1})
            result = self.verify.verify(data, self.package(directory, [[]], pictures_only=True))
            self.assertTrue(any("flattened" in error for error in result["errors"]))

    def test_verifier_rejects_source_changes_locks_and_page_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            data = self.inspect.inventory(source)
            pptx = self.package(directory, [["First", "Second"]], locked=True)
            result = self.verify.verify(data, pptx)
            self.assertTrue(any("lock" in error for error in result["errors"]))
            source.write_bytes(source.read_bytes()+b"\n")
            result = self.verify.verify(data, self.package(directory, [["First", "Second"], ["Extra"]]))
            self.assertTrue(any("changed" in error for error in result["errors"]))
            self.assertTrue(any("Slide count" in error for error in result["errors"]))

    def test_verifier_enforces_compatibility_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            pptx = self.package(directory, [["First", "Second"]])
            with zipfile.ZipFile(pptx, "a") as archive:
                archive.writestr("ppt/media/icon.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
            result = self.verify.verify(self.inspect.inventory(source), pptx)
            self.assertFalse(result["technical_checks_passed"])
            self.assertFalse(result["compatibility"]["checks_passed"])
            self.assertTrue(any("SVG part retained" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
