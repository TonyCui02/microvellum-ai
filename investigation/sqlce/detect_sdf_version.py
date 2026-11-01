"""Report the SQL Server Compact file-format version of an SDF file.

Requires the `sqlce` package:  py -m pip install sqlce
This reads the file header only; it does not modify the SDF.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect the SQL Server Compact format version of an .sdf file."
    )
    parser.add_argument("sdf", type=Path, help="Path to the SDF file")
    args = parser.parse_args()

    sdf_path = args.sdf.expanduser().resolve()
    if not sdf_path.is_file():
        print(f"Error: file not found: {sdf_path}", file=sys.stderr)
        return 2

    try:
        from sqlce import SqlceDatabase
    except ImportError:
        print(
            "The 'sqlce' package is not installed. Install it with:\n"
            "  py -m pip install sqlce",
            file=sys.stderr,
        )
        return 2

    try:
        version = SqlceDatabase.get_format_version_from_file(str(sdf_path))
    except Exception as exc:
        print(f"Could not read SDF format version: {exc}", file=sys.stderr)
        return 1

    version_name = getattr(version, "name", str(version))
    labels = {
        "SQLCE_30": "SQL Server Compact 3.0",
        "SQLCE_35": "SQL Server Compact 3.5",
        "SQLCE_35_SP2": "SQL Server Compact 3.5 SP2",
        "SQLCE_40": "SQL Server Compact 4.0",
        "UNKNOWN": "Unknown SQL Server Compact file format",
    }

    print(f"File: {sdf_path}")
    print(f"Format: {labels.get(version_name, version_name)}")
    print("Method: SDF header format field (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
