"""
Fill Arelle's web cache for every regime in cache_entrypoints.json, for baking into the sandbox image, and
check the result against the committed file pin list (taxonomy-cache.lock.json).

Runs in CI before the image build (.github/workflows/sandbox-image.yml) and locally:

    .tmp/venv/bin/python service/warm_cache.py --dest .tmp/taxonomy/cache \\
        --report .tmp/taxonomy/cache-manifest.json --lock service/taxonomy-cache.lock.json

Each regime loads online into its own cache directory, in parallel; the directories are then merged into
--dest. Any file Arelle can't retrieve fails the run. --report records the SHA-256 of every cached file.

With --lock, the run fails if the downloaded files differ from the pinned list in any way (a publisher changed,
added or removed a file, or cache_entrypoints.json changed), and prints how to review and accept the change.
--update-lock accepts it by rewriting the pin list. --check-report compares an existing report without warming.
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from arelle_cache import load_entry_points, load_manifest, manifest_digest, print_result

LOCAL_COMMAND = (
    "rm -rf .tmp/taxonomy && .tmp/venv/bin/python service/warm_cache.py "
    "--dest .tmp/taxonomy/cache --report .tmp/taxonomy/cache-manifest.json "
    "--lock service/taxonomy-cache.lock.json --update-lock"
)


def merge(source: Path, dest: Path) -> list[str]:
    """Copy source into dest. Returns files present in both with different content."""
    conflicts = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        target = dest / path.relative_to(source)
        if target.exists():
            if not filecmp.cmp(path, target, shallow=False):
                conflicts.append(str(target.relative_to(dest)))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return conflicts


def file_report(cache_dir: Path) -> dict:
    files = []
    for path in sorted(p for p in cache_dir.rglob("*") if p.is_file()):
        files.append({
            "path": path.relative_to(cache_dir).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return {"files": len(files), "bytes": sum(f["bytes"] for f in files), "entries": files}


def lock_from_report(report: dict) -> dict:
    return {
        "_comment": [
            "SHA-256 of every taxonomy file in the sandbox image's Arelle web cache, as produced by warm_cache.py.",
            "The image build fails if a fresh warm-up differs from this list. Do not edit by hand: to accept a",
            "change, follow the instructions the failed build prints (warm_cache.py --update-lock).",
        ],
        "entrypointsSha256": report["entrypointsSha256"],
        "files": {e["path"]: e["sha256"] for e in report["entries"]},
    }


def compare_with_lock(report: dict, lock: dict) -> list[str]:
    """Human-readable differences between a warm-up report and the pin list; empty if identical."""
    lines = []
    if report["entrypointsSha256"] != lock["entrypointsSha256"]:
        lines.append("cache_entrypoints.json has changed since the pin list was last updated.")
    pinned: dict[str, str] = lock["files"]
    actual = {e["path"]: e["sha256"] for e in report["entries"]}
    for label, paths in (
        ("changed (publisher now serves different content)", sorted(p for p in actual.keys() & pinned.keys()
                                                                     if actual[p] != pinned[p])),
        ("new (not in the pin list)", sorted(actual.keys() - pinned.keys())),
        ("missing (pinned but no longer fetched)", sorted(pinned.keys() - actual.keys())),
    ):
        if paths:
            lines.append(f"{len(paths)} file(s) {label}:")
            lines += [f"  {p}" for p in paths[:50]]
            if len(paths) > 50:
                lines.append(f"  ... {len(paths) - 50} more")
    return lines


def explain_mismatch(differences: list[str], lock_path: Path) -> None:
    text = "\n".join([
        "",
        "TAXONOMY CACHE DOES NOT MATCH THE PIN LIST",
        f"The warm-up result differs from {lock_path}:",
        "",
        *differences,
        "",
        "Nothing was built. Images already pushed are unaffected. To resolve:",
        "",
        "1. Find out why the files changed.",
        "   - If you edited service/cache_entrypoints.json, new and missing files are expected.",
        "   - Otherwise a publisher (ESMA, IFRS Foundation, SBR-NL / nltaxonomie.nl, XBRL International) has",
        "     changed files at the same URLs. Check their release notes or errata before accepting: the",
        "     image's validation results can change for the same filing.",
        "2. Re-warm locally and rewrite the pin list (about 15 minutes), from the repository root:",
        "     python3 -m venv .tmp/venv && .tmp/venv/bin/pip install -c requirements.txt -r service/requirements.txt",
        "       (once; skip if .tmp/venv exists)",
        f"     {LOCAL_COMMAND}",
        "   To compare a changed file, look it up under .tmp/taxonomy/cache/<path above>.",
        "3. Review the result:  git diff service/taxonomy-cache.lock.json",
        "4. Bump service/VERSION and commit taxonomy-cache.lock.json and VERSION together, stating in the",
        "   commit message which files changed and why.",
        "",
        "If the differences come and go between runs (a publisher serving unstable content), do not accept",
        "them: raise it with the team, as the affected URL needs a fixed source.",
    ])
    print(text, file=sys.stderr)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Taxonomy cache does not match the pin list\n\n```\n" + text.strip() + "\n```\n")


def check_lock(report: dict, lock_path: Path, update: bool) -> bool:
    if update:
        lock_path.write_text(json.dumps(lock_from_report(report), indent=1) + "\n", encoding="utf-8")
        print(f"pin list written: {lock_path} ({report['files']} files)")
        return True
    if not lock_path.is_file():
        explain_mismatch([f"{lock_path} does not exist yet."], lock_path)
        return False
    differences = compare_with_lock(report, json.loads(lock_path.read_text(encoding="utf-8")))
    if differences:
        explain_mismatch(differences, lock_path)
        return False
    print(f"pin list: all {report['files']} files match {lock_path}")
    return True


def warm(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[dict, bool]:
    if args.dest.exists() and any(args.dest.iterdir()):
        parser.error(f"{args.dest} already exists and is not empty")
    regimes = load_manifest()
    selected = args.regime or list(regimes)
    unknown = set(selected) - set(regimes)
    if unknown:
        parser.error(f"unknown regimes: {sorted(unknown)}")

    with tempfile.TemporaryDirectory(prefix="arelle-warm-") as work:
        def warm_regime(regime: str):
            cache = Path(work) / regime
            return cache, load_entry_points(regime, regimes[regime]["entryPoints"], cache, online=True)

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            outcomes = list(pool.map(warm_regime, selected))

        ok = True
        args.dest.mkdir(parents=True, exist_ok=True)
        for cache, result in outcomes:
            print_result(result)
            ok &= result.ok
            if cache.is_dir():
                for conflict in merge(cache, args.dest):
                    print(f"  WARNING {result.regime}: {conflict} differs from a copy fetched by another regime; "
                          "kept the first", file=sys.stderr)

    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entrypointsSha256": manifest_digest(),
        "regimes": {r: {"disclosureSystem": regimes[r]["disclosureSystem"],
                        "entryPoints": len(regimes[r]["entryPoints"])} for r in selected},
        **file_report(args.dest),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"cache: {report['files']} files, {report['bytes'] / 1024 / 1024:.0f} MB -> {args.dest}")
    print("warm-up " + ("passed" if ok else "FAILED"))
    return report, ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, help="Cache directory to create (must not exist).")
    parser.add_argument("--report", type=Path, help="Where to write the cache manifest JSON.")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--regime", action="append", help="Only these regimes (default: all; not with --lock).")
    parser.add_argument("--lock", type=Path, help="Pin list to check the result against.")
    parser.add_argument("--update-lock", action="store_true", help="Rewrite --lock from this run instead of checking.")
    parser.add_argument("--check-report", type=Path,
                        help="Compare this existing report with --lock, without warming.")
    args = parser.parse_args()

    if args.update_lock and not args.lock:
        parser.error("--update-lock needs --lock")
    if args.lock and args.regime:
        parser.error("--lock covers all regimes; don't combine it with --regime")

    if args.check_report:
        if not args.lock or args.update_lock:
            parser.error("--check-report needs --lock (and not --update-lock)")
        report = json.loads(args.check_report.read_text(encoding="utf-8"))
        current = manifest_digest()
        if report["entrypointsSha256"] != current:
            explain_mismatch([f"{args.check_report} was warmed from a different cache_entrypoints.json."],
                             args.lock)
            return 1
        return 0 if check_lock(report, args.lock, update=False) else 1

    if not args.dest or not args.report:
        parser.error("--dest and --report are required unless --check-report is given")
    report, ok = warm(args, parser)
    if not ok:
        if args.update_lock:
            print("pin list not updated: the warm-up failed", file=sys.stderr)
        return 1
    if args.lock and not check_lock(report, args.lock, args.update_lock):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
