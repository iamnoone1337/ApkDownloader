"""Flask web application server for the PlayStore Acquisition SDK."""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory

from playstore_client import AdbError, ApkDownloader
from bundle_merger import MergeError, merge_package_apks

DOWNLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__)


@dataclass
class Job:
    id: str
    url: str
    merge: bool = False
    status: str = "pending"  # pending|running|done|error
    message: str = ""
    package: Optional[str] = None
    apks: List[str] = field(default_factory=list)
    universal_apk: Optional[str] = None
    log: List[str] = field(default_factory=list)


JOBS: Dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _log(job: Job, msg: str) -> None:
    job.log.append(msg)


def _run_job(job_id: str, url: str) -> None:
    job = JOBS[job_id]
    job.status = "running"
    try:
        dl = ApkDownloader()
        _log(job, "Resolving package from URL...")
        package = dl.extract_package(url)
        job.package = package
        _log(job, f"Package: {package}")

        device = dl.ensure_device()
        _log(job, f"Using device: {device.serial}")

        if dl.is_installed(package):
            _log(job, "App already installed; skipping Play Store automation.")
        else:
            _log(job, "Launching Play Store page...")
            dl.open_play_store(package)
            time.sleep(4)
            _log(job, "Locating and tapping Install button...")
            if not dl.tap_install():
                raise AdbError("Install button not found on Play Store page.")
            _log(job, "Tapped Install. Waiting for install to complete...")
            if not dl.wait_for_install(package, timeout=600):
                raise AdbError("Install did not complete within 600s.")
            _log(job, "Install complete.")

        _log(job, "Pulling APK(s) from device...")
        apks = dl.pull_apk(package, out_dir=DOWNLOAD_DIR)
        job.apks = [os.path.basename(p) for p in apks]
        _log(job, f"Downloaded {len(apks)} APK file(s).")

        if job.merge:
            if len(apks) == 1:
                _log(job, "Single APK — no merge needed; using base.apk as universal.")
                job.universal_apk = os.path.basename(apks[0])
            else:
                _log(job, f"Merging {len(apks)} split APKs into a universal APK (APKEditor)...")
                universal = merge_package_apks(package, downloads_dir=DOWNLOAD_DIR)
                job.universal_apk = os.path.basename(universal)
                _log(job, f"Universal APK: {job.universal_apk} ({os.path.getsize(universal)} bytes)")

        job.status = "done"
        job.message = "Success"
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.message = str(e)
        _log(job, f"ERROR: {e}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/devices")
def api_devices():
    try:
        dl = ApkDownloader()
        return jsonify({"ok": True, "devices": [d.__dict__ for d in dl.list_devices()]})
    except AdbError as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    merge = bool(data.get("merge", False))
    if not url:
        return jsonify({"ok": False, "error": "Missing 'url'"}), 400
    job_id = uuid.uuid4().hex[:12]
    job = Job(id=job_id, url=url, merge=merge)
    with JOBS_LOCK:
        JOBS[job_id] = job
    t = threading.Thread(target=_run_job, args=(job_id, url), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/merge", methods=["POST"])
def api_merge():
    """Merge already-downloaded splits for a package into a universal APK."""
    data = request.get_json(force=True, silent=True) or {}
    package = (data.get("package") or "").strip()
    if not package:
        return jsonify({"ok": False, "error": "Missing 'package'"}), 400
    try:
        out = merge_package_apks(package, downloads_dir=DOWNLOAD_DIR)
        return jsonify({"ok": True, "universal_apk": os.path.basename(out),
                        "size": os.path.getsize(out)})
    except MergeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Unknown job"}), 404
    return jsonify({
        "ok": True,
        "id": job.id,
        "status": job.status,
        "message": job.message,
        "package": job.package,
        "apks": job.apks,
        "universal_apk": job.universal_apk,
        "log": job.log,
    })


@app.route("/downloads/<path:filename>")
def downloads(filename: str):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
