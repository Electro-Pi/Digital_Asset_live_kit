"""Copy the app's catalog into this repo, byte for byte, and check the result.

The frontend owns the catalog (``jahez-frontend/data/catalog.json``, built from
``data/catalog/*.json``). The agent keeps an identical copy because it is
deployed separately; agent-only data lives beside it in
``meal_attributes.json`` so the copy never has to differ from the app's.

    python scripts/sync_catalog.py                  # copy, then annotate new meals
    python scripts/sync_catalog.py --check          # exit 1 if the copies differ
    FRONTEND_CATALOG_PATH=/path/catalog.json python scripts/sync_catalog.py

The agent runs the same comparison at startup (``src.catalog.check_catalog_sync``)
and logs an error when the two have drifted.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.catalog import DEFAULT_CATALOG_PATH, catalog_differences, frontend_catalog_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="compare only, copy nothing")
    args = parser.parse_args()
    source = frontend_catalog_path()
    if source is None:
        print("frontend catalog not found; set FRONTEND_CATALOG_PATH", file=sys.stderr)
        return 2
    if args.check:
        diffs = catalog_differences(source, DEFAULT_CATALOG_PATH)
        for line in diffs[:50]:
            print(line)
        print(f"{len(diffs)} difference(s) between {source} and {DEFAULT_CATALOG_PATH}")
        return 1 if diffs else 0
    shutil.copyfile(source, DEFAULT_CATALOG_PATH)
    print(f"copied {source} -> {DEFAULT_CATALOG_PATH}")
    return subprocess.call([sys.executable, str(REPO_ROOT / "scripts" / "annotate_catalog.py")], env=os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
