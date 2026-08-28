"""Small synthetic packages test the guard, not Office rendering or font decoding."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_pptx_compatibility.py"
spec = importlib.util.spec_from_file_location("pptx_compat", SCRIPT)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = compat.DOCREL


def package_parts():
    return {
        "[Content_Types].xml": (
            f'<Types xmlns="{compat.CT}">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Default Extension="svg" ContentType="image/svg+xml"/>'
            f'<Default Extension="dat" ContentType="{compat.FONT_MIME}"/>'
            '</Types>'),
        "_rels/.rels": (
            f'<Relationships xmlns="{compat.REL}"><Relationship Id="root" '
            f'Type="{R}/officeDocument" Target="ppt/presentation.xml"/></Relationships>'),
        "ppt/presentation.xml": (
            f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="slide1"/></p:sldIdLst></p:presentation>'),
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{compat.REL}"><Relationship Id="slide1" '
            f'Type="{R}/slide" Target="slides/slide1.xml"/></Relationships>'),
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="{P}"/>',
    }


def eot_header():
    # Deliberately only a header fixture: this checker does not decode fonts.
    data = bytearray(86)
    struct.pack_into("<III", data, 0, len(data), 4, 0x20001)
    struct.pack_into("<H", data, 34, 0x504C)
    return bytes(data)


def png_header(width, height):
    return compat.PNG_MAGIC + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + bytes(9)


class CompatibilityTests(unittest.TestCase):
    def run_check(self, parts, duplicates=()):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.pptx"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(path, "w") as z:
                    for name, data in parts.items():
                        z.writestr(name, data)
                    for name in duplicates:
                        z.writestr(name, parts[name])
            before = path.read_bytes()
            result = compat.check(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(result["pptx_sha256"], hashlib.sha256(before).hexdigest())
            return result

    def test_basic_package_passes_without_claiming_app_tests(self):
        result = self.run_check(package_parts())
        self.assertTrue(result["checks_passed"], result)
        self.assertEqual(result["application_tests"], {"microsoft_powerpoint": "not-tested", "wps": "not-tested"})
        self.assertEqual(result["full_schema_validation"], "not-performed")

    def test_eot_payload_not_extension_controls_header_check(self):
        parts = package_parts()
        parts["ppt/fonts/font.dat"] = eot_header()
        self.assertTrue(self.run_check(parts)["checks_passed"])
        parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(compat.FONT_MIME, "application/vnd.ms-fontobject")
        result = self.run_check(parts)
        self.assertFalse(result["checks_passed"])
        self.assertTrue(any("expected EOT MIME" in e for e in result["errors"]))

    def test_raw_ttf_and_invalid_eot_headers_fail(self):
        for payload in [b"\x00\x01\x00\x00" + bytes(100), eot_header()[:-1],
                        eot_header()[:8] + bytes(4) + eot_header()[12:]]:
            with self.subTest(payload_length=len(payload)):
                parts = package_parts()
                parts["ppt/fonts/font.dat"] = payload
                self.assertFalse(self.run_check(parts)["checks_passed"])

    def test_svg_and_tiny_fallback_fail(self):
        parts = package_parts()
        parts["ppt/media/a.svg"] = '<svg xmlns="http://www.w3.org/2000/svg"/>'
        parts["ppt/media/a.png"] = png_header(1, 1)
        result = self.run_check(parts)
        self.assertTrue(any("SVG part retained" in e for e in result["errors"]))
        self.assertTrue(any("1x1 placeholder" in e for e in result["errors"]))

    def test_png_header_size_and_invalid_encoding(self):
        parts = package_parts()
        parts["ppt/media/icon.png"] = png_header(128, 64)
        result = self.run_check(parts)
        self.assertTrue(result["checks_passed"], result)
        self.assertEqual(result["image_parts"][0]["width"], 128)
        parts["ppt/media/icon.png"] = b"<svg/>"
        self.assertFalse(self.run_check(parts)["checks_passed"])

    def test_missing_target_and_unresolved_reference_fail(self):
        parts = package_parts()
        del parts["ppt/slides/slide1.xml"]
        self.assertTrue(any("Missing relationship target" in e for e in self.run_check(parts)["errors"]))
        parts = package_parts()
        parts["ppt/slides/slide1.xml"] = f'<p:sld xmlns:p="{P}" xmlns:r="{R}" r:id="missing"/>'
        self.assertTrue(any("Unresolved relationship reference" in e for e in self.run_check(parts)["errors"]))

    def test_external_images_fail_but_hyperlinks_do_not(self):
        parts = package_parts()
        rels = (f'<Relationships xmlns="{compat.REL}"><Relationship Id="pic" '
                f'Type="{R}/image" Target="https://example.com/a.png" TargetMode="External"/></Relationships>')
        parts["ppt/slides/_rels/slide1.xml.rels"] = rels
        self.assertTrue(any("External image/font" in e for e in self.run_check(parts)["errors"]))
        parts["ppt/slides/_rels/slide1.xml.rels"] = rels.replace("/image", "/hyperlink")
        self.assertTrue(self.run_check(parts)["checks_passed"])

    def test_content_types_and_xml_fail_closed(self):
        for target, value in [
            ("[Content_Types].xml", "<broken"),
            ("[Content_Types].xml", f'<Types xmlns="{compat.CT}"/>'),
            ("ppt/slides/slide1.xml", '<!DOCTYPE a [<!ENTITY x "x">]><a>&x;</a>'),
        ]:
            with self.subTest(target=target, value=value):
                parts = package_parts()
                parts[target] = value
                self.assertFalse(self.run_check(parts)["checks_passed"])

    def test_duplicate_members_and_relationships_fail(self):
        self.assertFalse(self.run_check(package_parts(), ["ppt/slides/slide1.xml"])["checks_passed"])
        parts = package_parts()
        text = parts["ppt/_rels/presentation.xml.rels"]
        element = text[text.index("<Relationship Id"):text.index("/>")+2]
        parts["ppt/_rels/presentation.xml.rels"] = text.replace("</Relationships>", element + "</Relationships>")
        self.assertTrue(any("Duplicate relationship ID" in e for e in self.run_check(parts)["errors"]))

    def test_percent_encoded_and_absolute_targets(self):
        self.assertEqual(compat.internal_target("ppt/presentation.xml", "/ppt/slides/a%20b.xml"), "ppt/slides/a b.xml")
        self.assertEqual(compat.internal_target("ppt/slides/slide1.xml", "../media/a.png"), "ppt/media/a.png")
        for target in ["../../../outside.png", "https://example.com/a.png", "..\\bad.png"]:
            with self.assertRaises(ValueError):
                compat.internal_target("ppt/presentation.xml", target)

    def test_cli_reports_errors_without_overwriting_input_or_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.pptx"
            path.write_bytes(b"not zip")
            report = Path(directory) / "report.json"
            run = lambda output: subprocess.run([sys.executable, "-B", str(SCRIPT), str(path),
                                                "--output", str(output)], capture_output=True)
            self.assertNotEqual(run(path).returncode, 0)
            self.assertEqual(path.read_bytes(), b"not zip")
            self.assertEqual(run(report).returncode, 1)
            self.assertFalse(json.loads(report.read_text())["checks_passed"])
            report.write_text("KEEP")
            self.assertNotEqual(run(report).returncode, 0)
            self.assertEqual(report.read_text(), "KEEP")


if __name__ == "__main__":
    unittest.main()
