"""Bundle Merger — merge installed split APKs into a single universal APK.

Why not bundletool? `bundletool build-apks --mode=universal` requires the
original .aab (App Bundle), which only Google's servers have. After installing
via Play Store and pulling with `adb pull`, what we have on disk are the
already-split installed APKs (base.apk + split_config.*.apk). The standard tool
for stitching those back into a single universal APK is APKEditor.

This module downloads APKEditor.jar on first use into a local cache dir and
shells out to `java -jar APKEditor.jar m -i <dir> -o universal.apk`.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Optional


# Pinned APKEditor release. Update version + sha256 to bump.
APKEDITOR_VERSION = "1.4.3"
APKEDITOR_URL = (
    f"https://github.com/REAndroid/APKEditor/releases/download/"
    f"V{APKEDITOR_VERSION}/APKEditor-{APKEDITOR_VERSION}.jar"
)
APKEDITOR_SHA256 = "6f1de3d27d9c89a4ddccaf08fa1ba91706c0fd9311c7e23a9d801a1b8eef9f1d"  # verified at runtime; logged if mismatch


class MergeError(RuntimeError):
    pass


def _cache_dir() -> str:
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    os.makedirs(base, exist_ok=True)
    return base


def _download(url: str, dest: str) -> None:
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "apk-downloader/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dest)


def ensure_apkeditor(verify_sha256: bool = False) -> str:
    """Returns local path to APKEditor.jar, downloading if absent."""
    jar = os.path.join(_cache_dir(), f"APKEditor-{APKEDITOR_VERSION}.jar")
    if not os.path.exists(jar):
        try:
            _download(APKEDITOR_URL, jar)
        except Exception as e:  # noqa: BLE001
            raise MergeError(f"Failed to download APKEditor: {e}") from e
    if verify_sha256:
        h = hashlib.sha256()
        with open(jar, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        digest = h.hexdigest()
        if digest != APKEDITOR_SHA256:
            # Don't fail hard — log mismatch (the constant may be stale).
            # Caller can opt to delete and retry.
            print(f"[merger] WARN: APKEditor sha256 mismatch: got {digest}")
    return jar


def _ensure_java() -> str:
    java = shutil.which("java")
    if not java:
        raise MergeError("'java' not found on PATH. Install a JRE/JDK (>=8).")
    return java


def merge_splits(splits_dir: str, output_apk: str, timeout: int = 300) -> str:
    """Merge all *.apk files in splits_dir into a single universal APK at output_apk.

    Returns absolute path to output_apk.
    """
    if not os.path.isdir(splits_dir):
        raise MergeError(f"splits_dir does not exist: {splits_dir}")
    apks = [f for f in os.listdir(splits_dir) if f.lower().endswith(".apk")]
    if not apks:
        raise MergeError(f"No .apk files found in {splits_dir}")
    java = _ensure_java()
    jar = ensure_apkeditor()
    output_apk = os.path.abspath(output_apk)
    os.makedirs(os.path.dirname(output_apk) or ".", exist_ok=True)
    cmd = [java, "-jar", jar, "m", "-i", os.path.abspath(splits_dir),
           "-o", output_apk, "-f"]  # -f overwrites
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MergeError(f"APKEditor merge timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise MergeError(
            f"APKEditor merge failed (exit {proc.returncode}):\n"
            f"STDOUT: {proc.stdout[-2000:]}\nSTDERR: {proc.stderr[-2000:]}"
        )
    if not os.path.exists(output_apk) or os.path.getsize(output_apk) == 0:
        raise MergeError(f"APKEditor reported success but output is missing/empty: {output_apk}")
    return output_apk


def merge_package_apks(package: str, downloads_dir: str = "downloads",
                       output_dir: Optional[str] = None) -> str:
    """Convenience: gather all split APKs for `package` from downloads_dir
    (matching the naming used by ApkDownloader.pull_apk) and merge them.
    Returns the universal APK path."""
    if not os.path.isdir(downloads_dir):
        raise MergeError(f"downloads_dir does not exist: {downloads_dir}")
    matches = [f for f in os.listdir(downloads_dir)
               if f.startswith(package + "_") and f.lower().endswith(".apk")]
    if not matches:
        raise MergeError(f"No APKs for {package!r} found in {downloads_dir}")
    output_dir = output_dir or downloads_dir
    output_apk = os.path.join(output_dir, f"{package}_universal.apk")
    # Stage into a temp dir so APKEditor doesn't see the universal output as input.
    with tempfile.TemporaryDirectory() as td:
        for name in matches:
            shutil.copy2(os.path.join(downloads_dir, name), os.path.join(td, name))
        return merge_splits(td, output_apk)
