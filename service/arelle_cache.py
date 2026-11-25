"""
Shared helpers for warm_cache.py (image build) and verify_image.py (image check): read the entry point
manifest and load entry points with arelleCmdLine against a given web cache directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("ARELLE_APP_DIR", Path(__file__).resolve().parent.parent))
MANIFEST_FILE = Path(__file__).with_name("cache_entrypoints.json")

# Logged at warning level by Arelle, but each means a file did not make it into (or out of) the cache.
FAILURE_CODES = {
    "IOerror",
    "FileNotLoadable",
    "arelle:discoveryError",
    "webCache:retrievalError",
    "webCache:unsuccessfulRetrieval",
    "webCache:invalidRetrieval",
    "webCache:contentTooShortError",
    "webCache:attemptingOfflineOperation",
}
FAILURE_LEVELS = {"error", "critical", "exception"}
# Logged at error level on each retry, even when the retry then succeeds; a final failure has its own code above.
IGNORED_CODES = {"webCache:retryingOperation"}

# Intermediate CA certificates for publishers whose servers don't send their full chain (see certs/README.md).
# Added to the CA bundle for online warm-up only; each must still chain to a root in certifi's bundle.
EXTRA_CA_DIR = Path(__file__).with_name("certs")


def _ca_bundle(work: Path) -> Path:
    import certifi  # in Arelle's requirements

    bundle = work / "ca-bundle.pem"
    parts = [Path(certifi.where()).read_text(encoding="ascii")]
    parts += [p.read_text(encoding="ascii") for p in sorted(EXTRA_CA_DIR.glob("*.pem"))]
    bundle.write_text("\n".join(parts), encoding="ascii")
    return bundle


@dataclass
class LoadResult:
    regime: str
    entry_points: int
    seconds: float
    returncode: int
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.problems


def load_manifest(path: Path = MANIFEST_FILE) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["regimes"]


def manifest_digest(path: Path = MANIFEST_FILE) -> str:
    """SHA-256 of the regimes and entry points only, so editing the file's comments doesn't count as a change."""
    import hashlib

    canonical = json.dumps(load_manifest(path), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_entry_points(regime: str, entry_points: list[str], cache_dir: Path, online: bool) -> LoadResult:
    """Load the entry points in one Arelle process. Online fills cache_dir; offline only reads it."""
    with tempfile.TemporaryDirectory(prefix=f"arelle-{regime}-") as work:
        log_file = Path(work) / "log.json"
        cmd = [
            sys.executable, str(APP_DIR / "arelleCmdLine.py"),
            "--internetConnectivity", "online" if online else "offline",
            "--cacheDirectory", str(cache_dir),
            # Filings mix http:// and https:// references to the same taxonomy files.
            "--httpsRedirectCache",
            "--file", "|".join(entry_points),
            "--logFile", str(log_file),
            "--logLevel", "warning",
        ]
        # Separate Arelle config dir per run, so parallel runs don't share config.json.
        env = {**os.environ, "XDG_CONFIG_HOME": str(Path(work) / "config")}
        if online:
            # Read by the OpenSSL default verify paths that truststore uses on Linux.
            env["SSL_CERT_FILE"] = str(_ca_bundle(Path(work)))
        started = time.monotonic()
        proc = subprocess.run(cmd, cwd=APP_DIR, env=env, capture_output=True, text=True, timeout=3600)
        seconds = time.monotonic() - started

        problems: list[str] = []
        if log_file.is_file():
            for entry in json.loads(log_file.read_text(encoding="utf-8") or '{"log": []}')["log"]:
                if entry["code"] in IGNORED_CODES:
                    continue
                if entry["level"] in FAILURE_LEVELS or entry["code"] in FAILURE_CODES:
                    problems.append(f"[{entry['code']}] {entry['message']['text']}")
        else:
            problems.append("Arelle wrote no log file")
        if "Traceback" in proc.stderr:
            problems.append(proc.stderr.strip()[-2000:])
    return LoadResult(regime, len(entry_points), seconds, proc.returncode, problems)


def print_result(result: LoadResult, max_problems: int = 20) -> None:
    status = "OK" if result.ok else "FAILED"
    print(f"{result.regime}: {result.entry_points} entry points, {result.seconds:.0f} s, {status}", flush=True)
    for problem in result.problems[:max_problems]:
        print(f"  {problem}", file=sys.stderr)
    if len(result.problems) > max_problems:
        print(f"  ... {len(result.problems) - max_problems} more", file=sys.stderr)
