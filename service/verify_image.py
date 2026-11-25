"""
Checks run inside the built sandbox image (CI runs it with `docker run --network none`; the post-push smoke
test runs it inside a real Vercel Sandbox):

1. Every file listed in the cache manifest is in the image with the recorded checksum.
2. Every regime's entry points load from the cache with Arelle offline, with no missing files.
3. The Arelle config directory (XDG_CONFIG_HOME) is writable.

Exits non-zero on any failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from arelle_cache import APP_DIR, load_entry_points, load_manifest, print_result

CACHE_DIR = Path(os.environ.get("ARELLE_CACHE_DIR", "/opt/arelle/cache"))
CACHE_MANIFEST = Path(os.environ.get("ARELLE_CACHE_MANIFEST", "/opt/arelle/cache-manifest.json"))


def check_cache_files() -> list[str]:
    report = json.loads(CACHE_MANIFEST.read_text(encoding="utf-8"))
    errors = []
    for entry in report["entries"]:
        path = CACHE_DIR / entry["path"]
        if not path.is_file():
            errors.append(f"missing cached file {path}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            errors.append(f"checksum mismatch {path}")
    print(f"cache files: {report['files'] - len(errors)}/{report['files']} match the manifest", flush=True)
    return errors


def check_writable_config() -> list[str]:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if not config_home:
        return ["XDG_CONFIG_HOME is not set"]
    try:
        Path(config_home).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=config_home):
            pass
    except OSError as exc:
        return [f"XDG_CONFIG_HOME {config_home} is not writable: {exc}"]
    return []


def main() -> int:
    build_info = APP_DIR / "BUILD_INFO.json"
    if build_info.is_file():
        print(build_info.read_text(encoding="utf-8").strip())

    errors = check_cache_files() + check_writable_config()
    regimes = load_manifest()
    # CPU-bound (each entry point parses its full DTS), so one Arelle process per regime in parallel.
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        results = list(pool.map(
            lambda item: load_entry_points(item[0], item[1]["entryPoints"], CACHE_DIR, online=False),
            regimes.items()))
    for regime, result in zip(regimes, results):
        print_result(result)
        errors += [f"{regime}: {p}" for p in result.problems]
        if result.returncode != 0:
            errors.append(f"{regime}: arelleCmdLine exited {result.returncode}")

    for error in errors[:50]:
        print(f"ERROR {error}", file=sys.stderr)
    print("image verification " + ("FAILED" if errors else "passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
