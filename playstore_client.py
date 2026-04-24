"""PlayStore Acquisition Client — ADB-based APK download SDK.

Usage:
    from playstore_client import ApkDownloader
    dl = ApkDownloader()
    pkg = dl.extract_package("https://play.google.com/store/apps/details?id=com.whatsapp")
    dl.open_play_store(pkg)
    dl.tap_install()
    dl.wait_for_install(pkg)
    apks = dl.pull_apk(pkg, out_dir="./downloads")
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlparse


class AdbError(RuntimeError):
    pass


@dataclass
class AdbDevice:
    serial: str
    state: str


class ApkDownloader:
    def __init__(self, adb_path: str = "adb", serial: Optional[str] = None, timeout: int = 30):
        self.adb_path = adb_path
        self.serial = serial
        self.timeout = timeout
        if shutil.which(adb_path) is None:
            raise AdbError(
                f"'{adb_path}' not found on PATH. Install Android platform-tools and ensure adb is available."
            )

    # ---------- adb plumbing ----------
    def _adb(self, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=self.timeout,
        )
        if check and proc.returncode != 0:
            raise AdbError(f"adb {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc

    def list_devices(self) -> List[AdbDevice]:
        proc = self._adb("devices")
        devices: List[AdbDevice] = []
        for line in proc.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append(AdbDevice(serial=parts[0], state=parts[1]))
        return devices

    def ensure_device(self) -> AdbDevice:
        devices = [d for d in self.list_devices() if d.state == "device"]
        if not devices:
            raise AdbError("No authorized device connected. Run 'adb devices' to verify.")
        if self.serial:
            for d in devices:
                if d.serial == self.serial:
                    return d
            raise AdbError(f"Device {self.serial} not found among connected devices.")
        return devices[0]

    # ---------- Play Store URL ----------
    @staticmethod
    def extract_package(url_or_pkg: str) -> str:
        """Accepts a Play Store URL or a raw package name, returns the package name."""
        s = url_or_pkg.strip()
        # Plain package name (heuristic: dotted, no spaces, no slashes)
        if "://" not in s and "/" not in s and " " not in s and "." in s:
            return s
        parsed = urlparse(s)
        qs = parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            pkg = qs["id"][0].strip()
        else:
            # Try path-based: /store/apps/details/id=com.foo or weird shapes
            m = re.search(r"id=([\w\.]+)", s)
            if not m:
                raise ValueError(f"Could not extract package name from: {url_or_pkg}")
            pkg = m.group(1)
        if not re.match(r"^[\w]+(\.[\w]+)+$", pkg):
            raise ValueError(f"Extracted package looks invalid: {pkg!r}")
        return pkg

    # ---------- Play Store automation ----------
    def is_installed(self, package: str) -> bool:
        proc = self._adb("shell", "pm", "list", "packages", package, check=False)
        for line in proc.stdout.splitlines():
            if line.strip() == f"package:{package}":
                return True
        return False

    def open_play_store(self, package: str) -> None:
        """Step A: launch Play Store on the app's details page."""
        url = f"market://details?id={package}"
        self._adb(
            "shell", "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", url,
        )

    def _ui_dump(self) -> str:
        """Dump current UI hierarchy XML."""
        # uiautomator writes to /sdcard/window_dump.xml
        self._adb("shell", "uiautomator", "dump", "/sdcard/window_dump.xml")
        proc = self._adb("shell", "cat", "/sdcard/window_dump.xml")
        return proc.stdout

    def _find_button_bounds(self, xml_text: str, labels: List[str]) -> Optional[tuple]:
        """Find a clickable node whose text/content-desc matches one of labels (case-insensitive)."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        wanted = {l.lower() for l in labels}
        for node in root.iter("node"):
            text = (node.attrib.get("text") or "").strip().lower()
            desc = (node.attrib.get("content-desc") or "").strip().lower()
            if text in wanted or desc in wanted:
                bounds = node.attrib.get("bounds")
                if not bounds:
                    continue
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return ((x1 + x2) // 2, (y1 + y2) // 2)
        return None

    def tap_install(self, max_wait: int = 20) -> bool:
        """Step B: locate and tap the Install button on the Play Store page."""
        labels = ["install"]
        deadline = time.time() + max_wait
        while time.time() < deadline:
            xml_text = self._ui_dump()
            point = self._find_button_bounds(xml_text, labels)
            if point:
                x, y = point
                self._adb("shell", "input", "tap", str(x), str(y))
                return True
            time.sleep(1.0)
        return False

    # Phrases the Play Store shows when an install cannot proceed.
    _INSTALL_ERROR_PHRASES = (
        "can't install",
        "couldn't install",
        "couldn't be installed",
        "not compatible with your device",
        "your device isn't compatible",
        "this app isn't available for your device",
        "item not found",
        "error code",
        "error retrieving information from server",
    )

    def detect_install_error(self) -> Optional[str]:
        """If a Play Store error dialog is showing, return its message; else None."""
        try:
            xml_text = self._ui_dump()
            root = ET.fromstring(xml_text)
        except (AdbError, ET.ParseError):
            return None
        for node in root.iter("node"):
            text = (node.attrib.get("text") or "").strip()
            if not text:
                continue
            low = text.lower()
            for phrase in self._INSTALL_ERROR_PHRASES:
                if phrase in low:
                    return text
        return None

    def wait_for_install(self, package: str, timeout: int = 600, poll: float = 3.0) -> bool:
        """Poll until the package appears in `pm list packages`, an error dialog
        appears, or timeout. Raises AdbError on a detected Play Store error."""
        deadline = time.time() + timeout
        # Check for error dialogs every ~6s (every other poll) to avoid uiautomator overhead.
        check_error_every = max(1, int(6 / max(poll, 0.1)))
        i = 0
        while time.time() < deadline:
            if self.is_installed(package):
                return True
            i += 1
            if i % check_error_every == 0:
                err = self.detect_install_error()
                if err:
                    raise AdbError(f"Play Store reported: {err!r}")
            time.sleep(poll)
        return False

    # ---------- APK extraction ----------
    def get_apk_paths(self, package: str) -> List[str]:
        proc = self._adb("shell", "pm", "path", package)
        paths = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                paths.append(line[len("package:"):])
        if not paths:
            raise AdbError(f"No APK paths returned by 'pm path {package}'. Is it installed?")
        return paths

    def pull_apk(self, package: str, out_dir: str = "downloads") -> List[str]:
        """Step C: pull APK file(s) from device to out_dir. Returns local paths."""
        os.makedirs(out_dir, exist_ok=True)
        remote_paths = self.get_apk_paths(package)
        local_paths: List[str] = []
        for i, remote in enumerate(remote_paths):
            base = os.path.basename(remote)
            # Avoid clashes when multiple split APKs share base names
            local_name = f"{package}_{i}_{base}" if len(remote_paths) > 1 else f"{package}_{base}"
            local = os.path.join(out_dir, local_name)
            self._adb("pull", remote, local)
            if not os.path.exists(local):
                raise AdbError(f"adb pull reported success but file missing: {local}")
            local_paths.append(local)
        return local_paths

    # ---------- Orchestration ----------
    def download_from_url(self, url_or_pkg: str, out_dir: str = "downloads",
                          install_timeout: int = 600, merge_universal: bool = False) -> dict:
        package = self.extract_package(url_or_pkg)
        device = self.ensure_device()
        already = self.is_installed(package)
        steps = {"package": package, "device": device.serial, "already_installed": already,
                 "tapped": False, "installed": already, "apks": [], "universal_apk": None}
        if not already:
            self.open_play_store(package)
            time.sleep(4)  # let Play Store render
            steps["tapped"] = self.tap_install()
            if not steps["tapped"]:
                raise AdbError(
                    "Could not locate the Install button. The Play Store page may show "
                    "'Open' (already installed), require sign-in, or this region/account "
                    "lacks access to the app."
                )
            steps["installed"] = self.wait_for_install(package, timeout=install_timeout)
            if not steps["installed"]:
                raise AdbError(f"Install did not complete within {install_timeout}s for {package}.")
        steps["apks"] = self.pull_apk(package, out_dir=out_dir)
        if merge_universal and len(steps["apks"]) > 1:
            from bundle_merger import merge_package_apks  # lazy import (java/jar optional)
            steps["universal_apk"] = merge_package_apks(package, downloads_dir=out_dir)
        elif merge_universal and len(steps["apks"]) == 1:
            # Single APK; "universal" is just that APK.
            steps["universal_apk"] = steps["apks"][0]
        return steps
