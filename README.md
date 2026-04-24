# PlayStore Acquisition SDK

> **Enterprise-grade toolkit for automated APK acquisition from Google Play Store via ADB.**

A production-ready Python SDK and web interface that automates downloading APK files from the Google Play Store by orchestrating a real Android device over ADB. Built for security teams, mobile QA engineers, and DevSecOps pipelines that need reliable APK extraction for SAST, reverse engineering, or compliance auditing.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [SDK Reference](#sdk-reference)
  - [PlayStore Client (`playstore_client.py`)](#playstore-client)
  - [Bundle Merger (`bundle_merger.py`)](#bundle-merger)
- [Web Server & REST API](#web-server--rest-api)
- [Integration Examples](#integration-examples)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)

---

## Architecture Overview

```
┌──────────────────────────────────────────────┐
│               Your Application               │
├──────────┬───────────────┬───────────────────┤
│  SDK API │  REST API     │   Web Dashboard   │
│ (import) │  (HTTP POST)  │   (Browser)       │
├──────────┴───────────────┴───────────────────┤
│         PlayStore Acquisition SDK            │
│  ┌──────────────────┐ ┌───────────────────┐  │
│  │ playstore_client │ │  bundle_merger    │  │
│  │  • URL parsing   │ │  • APKEditor.jar  │  │
│  │  • ADB commands  │ │  • Split → Single │  │
│  │  • UI automation │ │  • SHA-256 verify │  │
│  └────────┬─────────┘ └────────┬──────────┘  │
│           │                    │              │
│    ┌──────┴────────────────────┴──────┐       │
│    │         server.py (Flask)        │       │
│    │  • Job queue & async workers     │       │
│    │  • REST endpoints                │       │
│    │  • File serving                  │       │
│    └─────────────────────────────────┘       │
└──────────────────┬───────────────────────────┘
                   │ ADB (USB / TCP)
           ┌───────┴────────┐
           │ Android Device │
           │  (Play Store)  │
           └────────────────┘
```

---

## How It Works

1. **URL Parsing** — Extracts the package name from a Play Store URL (`?id=com.example.app`) or accepts a raw package name directly.
2. **Play Store Launch** — Opens the Play Store details page on the connected device via `adb shell am start`.
3. **UI Automation** — Uses `uiautomator dump` to scan the UI hierarchy XML and locate the **Install** button by text/content-desc matching, then taps it via `adb shell input tap`.
4. **Install Monitoring** — Polls `pm list packages` until the package appears. Detects Play Store error dialogs (e.g., "Can't install", "Not compatible") and raises descriptive errors.
5. **APK Extraction** — Resolves installed APK path(s) with `pm path` and pulls them with `adb pull`. Handles both single APKs and split APKs (App Bundles).
6. **Bundle Merging** *(Optional)* — Merges `base.apk` + `split_config.*.apk` into a single **universal APK** using [APKEditor](https://github.com/REAndroid/APKEditor) (auto-downloaded on first use). This is essential for SAST tools like MobSF, Jadx, and apktool that expect a single APK.

> **Why APKEditor instead of `bundletool`?** `bundletool build-apks --mode=universal` requires the original `.aab` (App Bundle) which only Google's servers possess. After Play Store installation, only the split APKs exist on-device — APKEditor is the industry-standard tool for reassembling them.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.9+ | With `pip` |
| **Android Platform Tools** | Latest | `adb` must be on your system `PATH` |
| **Java (JRE/JDK)** | 8+ | Only required for the universal-merge feature |
| **Android Device** | — | USB debugging enabled, authorized, signed in to Play Store |

---

## Installation

### 1. Clone or copy the SDK

```bash
git clone <repository-url>
cd scanner
```

### 2. Create a virtual environment & install dependencies

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Verify ADB connectivity

```bash
adb devices
# Expected output:
# List of devices attached
# XXXXXXXX    device
```

> The device must show state `device` (not `unauthorized` or `offline`).

---

## Quick Start

### Option A: Use the Web Dashboard

```bash
python server.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser. Paste a Play Store URL, click **Download APK**, and monitor the real-time job log.

### Option B: Use the SDK in your Python code

```python
from playstore_client import ApkDownloader

dl = ApkDownloader()
result = dl.download_from_url(
    "https://play.google.com/store/apps/details?id=com.whatsapp",
    merge_universal=True,
)

print("Package:", result["package"])
print("APK files:", result["apks"])
print("Universal APK:", result["universal_apk"])
```

### Option C: Use the REST API

```bash
# Start the server
python server.py

# Submit a download job
curl -X POST http://127.0.0.1:5000/api/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://play.google.com/store/apps/details?id=com.whatsapp", "merge": true}'

# Poll job status
curl http://127.0.0.1:5000/api/jobs/<job_id>
```

---

## SDK Reference

### PlayStore Client

**Module:** `playstore_client.py`  
**Main Class:** `ApkDownloader`

#### Constructor

```python
ApkDownloader(adb_path="adb", serial=None, timeout=30)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `adb_path` | `str` | `"adb"` | Path to the `adb` binary |
| `serial` | `str \| None` | `None` | Target a specific device by serial number |
| `timeout` | `int` | `30` | Timeout in seconds for individual ADB commands |

**Raises:** `AdbError` if the `adb` binary is not found on `PATH`.

#### Methods

##### `extract_package(url_or_pkg: str) -> str` *(static)*

Extracts a package name from a Play Store URL or validates a raw package name.

```python
pkg = ApkDownloader.extract_package(
    "https://play.google.com/store/apps/details?id=com.spotify.music&hl=en"
)
# Returns: "com.spotify.music"

pkg = ApkDownloader.extract_package("com.example.myapp")
# Returns: "com.example.myapp"
```

**Supported URL formats:**
- `https://play.google.com/store/apps/details?id=com.example.app`
- `market://details?id=com.example.app`
- Raw package name: `com.example.app`

**Raises:** `ValueError` for invalid or unparseable input.

---

##### `list_devices() -> List[AdbDevice]`

Returns all devices visible to ADB.

```python
dl = ApkDownloader()
for device in dl.list_devices():
    print(f"{device.serial} — {device.state}")
```

---

##### `ensure_device() -> AdbDevice`

Returns the first authorized device, or raises `AdbError` if none is available. If `serial` was provided in the constructor, validates that specific device is connected.

---

##### `is_installed(package: str) -> bool`

Checks whether a package is currently installed on the device.

```python
if dl.is_installed("com.whatsapp"):
    print("WhatsApp is already installed")
```

---

##### `open_play_store(package: str) -> None`

Launches the Play Store details page for the given package on the device.

---

##### `tap_install(max_wait: int = 20) -> bool`

Scans the device UI for an "Install" button and taps it. Returns `True` if successful, `False` if the button was not found within `max_wait` seconds.

---

##### `wait_for_install(package: str, timeout: int = 600, poll: float = 3.0) -> bool`

Blocks until the package is installed or timeout is reached. Automatically detects Play Store error dialogs and raises `AdbError` with the error message.

---

##### `detect_install_error() -> Optional[str]`

Checks the current device UI for Play Store error messages. Returns the error text if found, `None` otherwise.

**Detected error phrases:**
- "Can't install" / "Couldn't install"
- "Not compatible with your device"
- "This app isn't available for your device"
- "Item not found"
- "Error retrieving information from server"

---

##### `get_apk_paths(package: str) -> List[str]`

Returns the on-device file paths for all APK splits of an installed package.

```python
paths = dl.get_apk_paths("com.whatsapp")
# ["/data/app/~~abc123==/com.whatsapp-def456==/base.apk",
#  "/data/app/~~abc123==/com.whatsapp-def456==/split_config.arm64_v8a.apk", ...]
```

---

##### `pull_apk(package: str, out_dir: str = "downloads") -> List[str]`

Pulls all APK files for the given package from the device to the local `out_dir`. Returns a list of local file paths.

```python
apks = dl.pull_apk("com.whatsapp", out_dir="./output")
for apk in apks:
    print(f"Downloaded: {apk}")
```

---

##### `download_from_url(url_or_pkg, out_dir="downloads", install_timeout=600, merge_universal=False) -> dict`

**End-to-end orchestration method.** Parses the URL, installs the app (if needed), pulls the APKs, and optionally merges split APKs into a universal APK.

```python
result = dl.download_from_url(
    "https://play.google.com/store/apps/details?id=com.whatsapp",
    out_dir="./downloads",
    install_timeout=600,
    merge_universal=True,
)
```

**Returns a dictionary:**

| Key | Type | Description |
|---|---|---|
| `package` | `str` | Resolved package name |
| `device` | `str` | Device serial used |
| `already_installed` | `bool` | Whether the app was already on the device |
| `tapped` | `bool` | Whether the Install button was tapped |
| `installed` | `bool` | Whether the app is now installed |
| `apks` | `List[str]` | Local paths to downloaded APK files |
| `universal_apk` | `str \| None` | Path to the merged universal APK (if requested) |

---

#### Exceptions

| Exception | Base | When |
|---|---|---|
| `AdbError` | `RuntimeError` | ADB command failure, device not found, install failure |

---

### Bundle Merger

**Module:** `bundle_merger.py`

#### `merge_splits(splits_dir: str, output_apk: str, timeout: int = 300) -> str`

Merges all `.apk` files in `splits_dir` into a single universal APK. Uses APKEditor (auto-downloaded to `vendor/`).

```python
from bundle_merger import merge_splits

universal = merge_splits(
    splits_dir="./my_splits/",
    output_apk="./output/app_universal.apk",
)
print(f"Universal APK: {universal}")
```

**Raises:** `MergeError` on failure (missing Java, empty directory, APKEditor error).

---

#### `merge_package_apks(package: str, downloads_dir="downloads", output_dir=None) -> str`

Convenience wrapper that finds all split APKs for a given package name (using the naming convention from `ApkDownloader.pull_apk`) and merges them.

```python
from bundle_merger import merge_package_apks

universal = merge_package_apks("com.whatsapp", downloads_dir="./downloads")
```

---

#### `ensure_apkeditor(verify_sha256: bool = False) -> str`

Downloads APKEditor JAR to `vendor/` if not already cached. Returns the local path.

---

#### Exceptions

| Exception | Base | When |
|---|---|---|
| `MergeError` | `RuntimeError` | Java not found, merge failure, download failure |

---

## Web Server & REST API

**Module:** `server.py`

Start the server:
```bash
python server.py
# Listening on http://127.0.0.1:5000
```

### Endpoints

#### `GET /`

Serves the web dashboard UI.

---

#### `GET /api/devices`

Lists connected ADB devices.

**Response:**
```json
{
  "ok": true,
  "devices": [
    {"serial": "XXXXXXXX", "state": "device"}
  ]
}
```

---

#### `POST /api/download`

Submits a new download job (runs asynchronously in a background thread).

**Request body:**
```json
{
  "url": "https://play.google.com/store/apps/details?id=com.whatsapp",
  "merge": true
}
```

**Response:**
```json
{
  "ok": true,
  "job_id": "a1b2c3d4e5f6"
}
```

---

#### `GET /api/jobs/<job_id>`

Returns the current status and log of a download job.

**Response:**
```json
{
  "ok": true,
  "id": "a1b2c3d4e5f6",
  "status": "done",
  "message": "Success",
  "package": "com.whatsapp",
  "apks": ["com.whatsapp_0_base.apk", "com.whatsapp_1_split_config.arm64_v8a.apk"],
  "universal_apk": "com.whatsapp_universal.apk",
  "log": ["Resolving package from URL...", "Package: com.whatsapp", "..."]
}
```

**Job statuses:** `pending` → `running` → `done` | `error`

---

#### `POST /api/merge`

Merges already-downloaded split APKs for a package.

**Request body:**
```json
{
  "package": "com.whatsapp"
}
```

**Response:**
```json
{
  "ok": true,
  "universal_apk": "com.whatsapp_universal.apk",
  "size": 45678912
}
```

---

#### `GET /downloads/<filename>`

Download a pulled or merged APK file.

---

## Integration Examples

### CI/CD Pipeline Integration

```python
"""Example: Download APKs in a CI pipeline for automated SAST scanning."""
from playstore_client import ApkDownloader, AdbError

def acquire_apk_for_scanning(package_name: str, output_dir: str = "./artifacts") -> str:
    """Download and merge an APK, returning the universal APK path."""
    try:
        dl = ApkDownloader()
        result = dl.download_from_url(
            package_name,
            out_dir=output_dir,
            merge_universal=True,
            install_timeout=300,
        )
        if result["universal_apk"]:
            return result["universal_apk"]
        return result["apks"][0]
    except AdbError as e:
        raise SystemExit(f"APK acquisition failed: {e}")

# Usage
apk_path = acquire_apk_for_scanning("com.example.targetapp")
print(f"Ready for scanning: {apk_path}")
```

### Batch Download Script

```python
"""Example: Download multiple APKs from a list."""
from playstore_client import ApkDownloader, AdbError

PACKAGES = [
    "com.whatsapp",
    "com.spotify.music",
    "org.mozilla.firefox",
]

dl = ApkDownloader()

for pkg in PACKAGES:
    try:
        result = dl.download_from_url(pkg, merge_universal=True)
        print(f"[OK] {pkg} → {result['universal_apk'] or result['apks']}")
    except AdbError as e:
        print(f"[FAIL] {pkg}: {e}")
```

### Manual Step-by-Step Control

```python
"""Example: Fine-grained control over each acquisition step."""
from playstore_client import ApkDownloader
from bundle_merger import merge_package_apks

dl = ApkDownloader()

# Step 1: Parse URL
package = dl.extract_package("https://play.google.com/store/apps/details?id=com.whatsapp")

# Step 2: Check device
device = dl.ensure_device()
print(f"Using device: {device.serial}")

# Step 3: Check if already installed
if not dl.is_installed(package):
    dl.open_play_store(package)
    import time; time.sleep(4)
    dl.tap_install()
    dl.wait_for_install(package, timeout=300)

# Step 4: Pull APKs
apks = dl.pull_apk(package, out_dir="./downloads")
print(f"Pulled {len(apks)} file(s)")

# Step 5: Merge (optional)
if len(apks) > 1:
    universal = merge_package_apks(package, downloads_dir="./downloads")
    print(f"Universal APK: {universal}")
```

---

## Testing

The test suite covers all pure-logic components without requiring a physical device.

```bash
# Run with unittest
python -m unittest test_suite -v

# Run with pytest
python -m pytest test_suite.py -v
```

### What's tested

| Test Class | Coverage |
|---|---|
| `TestExtractPackage` | URL parsing, edge cases, invalid inputs |
| `TestFindButtonBounds` | UI hierarchy XML parsing, button detection |
| `TestDetectInstallError` | Play Store error dialog recognition |
| `TestAdbInit` | ADB binary validation |
| `TestMergerValidation` | Input validation for merge operations |

---

## Project Structure

```
scanner/
├── playstore_client.py     # Core SDK — ADB automation, APK extraction
├── bundle_merger.py        # Split APK → universal APK merging
├── server.py               # Flask web server & REST API
├── test_suite.py           # Comprehensive unit tests
├── requirements.txt        # Python dependencies
├── README.md               # This file
├── downloads/              # Default output directory for APK files
├── templates/
│   └── index.html          # Web dashboard UI
└── vendor/
    └── APKEditor-*.jar     # Auto-downloaded merge tool (cached)
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `AdbError: 'adb' not found on PATH` | Install [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) and add to your system `PATH` |
| `AdbError: No authorized device connected` | Enable USB debugging on your device, connect via USB, and accept the authorization prompt |
| `Install button not found` | The app may already be installed ("Open" button shown), require payment, or be unavailable in your region |
| `MergeError: 'java' not found on PATH` | Install a JRE/JDK (version 8+) and add to `PATH` |
| `Install did not complete within Xs` | Increase `install_timeout` — large apps on slow networks may need more time |
| Play Store shows "Can't install" | The app may be incompatible with the connected device's architecture or Android version |

---

## Known Limitations

- **Play Store UI changes** — The Install button is matched by `text`/`content-desc` == "Install". Google A/B tests or locale changes may require updating the labels in `tap_install()`.
- **Account & region restrictions** — Apps unavailable in your region/account or requiring payment will fail with a clear error.
- **DRM / Play Asset Delivery** — Some apps rely on PAD modules not included in the base APK. The SDK pulls everything `pm path` returns.
- **No root required** — Works with standard user-app APKs via `pm path` + `adb pull`. System apps on protected partitions may need root.
- **Render timing** — A 4-second delay allows the Play Store page to load before UI scanning. Slow devices may need a longer delay (configurable in `download_from_url()`).
