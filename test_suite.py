"""Comprehensive test suite for the PlayStore Acquisition SDK.

Run:  python -m pytest test_suite.py -v
  or: python -m unittest test_suite -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from playstore_client import AdbError, ApkDownloader
from bundle_merger import MergeError, merge_splits


# ---------------------------------------------------------------------------
# URL / package extraction (pure logic — no device needed)
# ---------------------------------------------------------------------------
class TestExtractPackage(unittest.TestCase):
    """Validate Play Store URL parsing and edge cases."""

    def test_standard_url(self):
        self.assertEqual(
            ApkDownloader.extract_package(
                "https://play.google.com/store/apps/details?id=com.whatsapp"),
            "com.whatsapp")

    def test_url_with_extra_params(self):
        self.assertEqual(
            ApkDownloader.extract_package(
                "https://play.google.com/store/apps/details?id=com.spotify.music&hl=en&gl=US"),
            "com.spotify.music")

    def test_market_url(self):
        self.assertEqual(
            ApkDownloader.extract_package("market://details?id=org.mozilla.firefox"),
            "org.mozilla.firefox")

    def test_raw_package(self):
        self.assertEqual(
            ApkDownloader.extract_package("com.example.app"), "com.example.app")

    def test_deep_nested_package(self):
        self.assertEqual(
            ApkDownloader.extract_package("com.a.b.c.d.e"), "com.a.b.c.d.e")

    def test_whitespace_trimmed(self):
        self.assertEqual(
            ApkDownloader.extract_package("  com.example.app  "), "com.example.app")

    def test_url_whitespace_trimmed(self):
        self.assertEqual(
            ApkDownloader.extract_package(
                "  https://play.google.com/store/apps/details?id=com.whatsapp  "),
            "com.whatsapp")

    def test_invalid_url_no_id(self):
        with self.assertRaises(ValueError):
            ApkDownloader.extract_package("https://example.com/no-id-here")

    def test_invalid_single_segment(self):
        """A bare word without dots is not a valid package name."""
        with self.assertRaises(ValueError):
            ApkDownloader.extract_package("https://play.google.com/store/apps/details?id=notapackage")

    def test_empty_string(self):
        with self.assertRaises(ValueError):
            ApkDownloader.extract_package("")

    def test_spaces_only(self):
        with self.assertRaises(ValueError):
            ApkDownloader.extract_package("   ")

    def test_id_with_underscores(self):
        self.assertEqual(
            ApkDownloader.extract_package(
                "https://play.google.com/store/apps/details?id=com.my_app.v2"),
            "com.my_app.v2")


# ---------------------------------------------------------------------------
# UI button finder (pure XML logic — no device needed)
# ---------------------------------------------------------------------------
class TestFindButtonBounds(unittest.TestCase):
    """Test the UI hierarchy parser that locates the Install button."""

    def _make_dl(self):
        with patch("shutil.which", return_value="/usr/bin/adb"):
            return ApkDownloader()

    def test_finds_install_button(self):
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" content-desc="" bounds="[0,0][100,100]"/>
  <node text="Install" content-desc="" bounds="[200,300][600,400]"/>
</hierarchy>'''
        dl = self._make_dl()
        self.assertEqual(dl._find_button_bounds(xml, ["install"]), (400, 350))

    def test_matches_content_desc(self):
        xml = '''<hierarchy>
  <node text="" content-desc="Install" bounds="[10,20][30,40]"/>
</hierarchy>'''
        dl = self._make_dl()
        self.assertEqual(dl._find_button_bounds(xml, ["install"]), (20, 30))

    def test_case_insensitive(self):
        xml = '<hierarchy><node text="INSTALL" bounds="[0,0][100,200]"/></hierarchy>'
        dl = self._make_dl()
        self.assertEqual(dl._find_button_bounds(xml, ["install"]), (50, 100))

    def test_no_match(self):
        xml = '<hierarchy><node text="Open" bounds="[0,0][100,200]"/></hierarchy>'
        dl = self._make_dl()
        self.assertIsNone(dl._find_button_bounds(xml, ["install"]))

    def test_no_bounds_attr(self):
        xml = '<hierarchy><node text="Install"/></hierarchy>'
        dl = self._make_dl()
        self.assertIsNone(dl._find_button_bounds(xml, ["install"]))

    def test_malformed_xml(self):
        dl = self._make_dl()
        self.assertIsNone(dl._find_button_bounds("<<not-xml>>", ["install"]))

    def test_empty_xml(self):
        dl = self._make_dl()
        self.assertIsNone(dl._find_button_bounds("", ["install"]))


# ---------------------------------------------------------------------------
# Install error detection (pure XML logic)
# ---------------------------------------------------------------------------
class TestDetectInstallError(unittest.TestCase):
    def _make_dl(self):
        with patch("shutil.which", return_value="/usr/bin/adb"):
            return ApkDownloader()

    def test_detects_cant_install(self):
        dl = self._make_dl()
        xml = '<hierarchy><node text="Can\'t install App" bounds="[0,0][1,1]"/></hierarchy>'
        with patch.object(dl, "_ui_dump", return_value=xml):
            self.assertIn("Can't install", dl.detect_install_error())

    def test_no_error(self):
        dl = self._make_dl()
        xml = '<hierarchy><node text="Install" bounds="[0,0][1,1]"/></hierarchy>'
        with patch.object(dl, "_ui_dump", return_value=xml):
            self.assertIsNone(dl.detect_install_error())

    def test_adb_failure_returns_none(self):
        dl = self._make_dl()
        with patch.object(dl, "_ui_dump", side_effect=AdbError("fail")):
            self.assertIsNone(dl.detect_install_error())


# ---------------------------------------------------------------------------
# ADB init validation
# ---------------------------------------------------------------------------
class TestAdbInit(unittest.TestCase):
    def test_missing_adb_raises(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(AdbError):
                ApkDownloader(adb_path="nonexistent_adb")


# ---------------------------------------------------------------------------
# Merger validation (no device, no Java needed for input-validation paths)
# ---------------------------------------------------------------------------
class TestMergerValidation(unittest.TestCase):
    def test_missing_splits_dir(self):
        with self.assertRaises(MergeError):
            merge_splits("/nonexistent/dir", "out.apk")

    def test_empty_splits_dir(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(MergeError):
                merge_splits(td, os.path.join(td, "out.apk"))


if __name__ == "__main__":
    unittest.main()
