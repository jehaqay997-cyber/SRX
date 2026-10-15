from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from srx_api.backup import create_sqlite_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and verify an SRX SQLite backup.")
    parser.add_argument("source", help="Path to the live SQLite database")
    parser.add_argument("destination", help="Path for the backup file")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing backup file")
    args = parser.parse_args()
    try:
        result = create_sqlite_backup(args.source, args.destination, overwrite=args.overwrite)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified SQLite backup created: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
