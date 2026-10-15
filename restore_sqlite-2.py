from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from srx_api.backup import restore_sqlite_backup  # noqa: E402

def main() -> int:
    parser = argparse.ArgumentParser(description="Restore and verify an SRX SQLite backup to a separate path.")
    parser.add_argument("backup", help="Path to the verified SQLite backup")
    parser.add_argument("destination", help="New path for the restored database")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing destination")
    args = parser.parse_args()
    try:
        result = restore_sqlite_backup(args.backup, args.destination, overwrite=args.overwrite)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified SQLite restore created: {result}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
