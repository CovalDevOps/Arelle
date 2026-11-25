"""
Add a regime's taxonomy entry points to cache_entrypoints.json, read from official taxonomy package zips.

Maintenance tool, run by hand when a taxonomy year is added (not part of the image build). The packages
are only read for their META-INF/taxonomyPackage.xml; the image build fetches the files themselves from the
publishers' URLs through Arelle's web cache (see warm_cache.py).

    python -I service/entrypoints_from_package.py --regime esef-2024 --disclosure-system esef-2024 \\
        --source-url https://www.esma.europa.eu/sites/default/files/2025-01/esef_taxonomy_2024.zip \\
        .tmp/taxonomies/esef_taxonomy_2024.zip

Repeat --extra for entry points a package doesn't declare (e.g. a taxonomy whose zip is no longer published).
Existing entries for the regime are replaced. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

TP_NAMESPACES = (
    "http://xbrl.org/2016/taxonomy-package",
    "http://xbrl.org/PWD/2015-01-14/taxonomy-package",
    "http://xbrl.org/PWD/2014-01-15/taxonomy-package",
)


def package_entry_points(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as z:
        meta = [n for n in z.namelist() if n.endswith("META-INF/taxonomyPackage.xml")]
        if len(meta) != 1:
            raise SystemExit(f"{zip_path}: expected one META-INF/taxonomyPackage.xml, found {len(meta)}")
        root = ET.fromstring(z.read(meta[0]))
    hrefs: list[str] = []
    for ns in TP_NAMESPACES:
        hrefs += [e.get("href", "") for e in root.iter(f"{{{ns}}}entryPointDocument")]
    hrefs = [h for h in hrefs if h.startswith(("http://", "https://"))]
    if not hrefs:
        raise SystemExit(f"{zip_path}: no absolute entry point URLs found")
    return hrefs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("cache_entrypoints.json"))
    parser.add_argument("--regime", required=True, help="Key in the manifest, e.g. esef-2024.")
    parser.add_argument("--disclosure-system", help="Arelle disclosure system name used to validate this regime.")
    parser.add_argument("--source-url", action="append", default=[],
                        help="Where the package zip was downloaded from (recorded for provenance).")
    parser.add_argument("--extra", action="append", default=[], help="Additional entry point URL.")
    parser.add_argument("packages", nargs="*", type=Path, help="Taxonomy package zips.")
    args = parser.parse_args()

    entry_points: list[str] = []
    for package in args.packages:
        found = package_entry_points(package)
        print(f"{package.name}: {len(found)} entry points")
        entry_points += found
    entry_points += args.extra
    entry_points = list(dict.fromkeys(entry_points))  # de-duplicate, keep order
    if not entry_points:
        parser.error("no entry points: pass package zips and/or --extra")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest.exists() else {"regimes": {}}
    manifest["regimes"][args.regime] = {
        "disclosureSystem": args.disclosure_system,
        "packageSources": args.source_url,
        "entryPoints": entry_points,
    }
    manifest["regimes"] = dict(sorted(manifest["regimes"].items()))
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{args.regime}: {len(entry_points)} entry points written to {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
