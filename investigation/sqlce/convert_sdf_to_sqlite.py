"""Export an SDF through SQL CE Toolbox into a read-only-analysis SQLite copy.

The exporter may modify its input while opening it, so it is given only a
temporary working copy. The output is assembled from the ordered SQL chunks
and is never a Microvellum factory database.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile


INDEX_RE = re.compile(
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+\[([^]]+)\]\s+ON\s+\[([^]]+)\]",
    re.IGNORECASE,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def qualify_indexes(sql: str) -> str:
    return INDEX_RE.sub(
        lambda match: (
            f"CREATE {match.group(1) or ''}INDEX "
            f"[{match.group(2)}__{match.group(3)}] ON [{match.group(3)}]"
        ),
        sql,
    )


def convert(source: Path, output: Path, exporter: Path) -> None:
    if not source.is_file() or source.suffix.lower() != ".sdf":
        raise ValueError(f"SDF source does not exist: {source}")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    if not exporter.is_file():
        raise FileNotFoundError(f"SQL CE Toolbox exporter not found: {exporter}")

    source_hash = sha256(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mv-sdf-convert-") as temp_name:
        temp = Path(temp_name)
        working_sdf = temp / source.name
        shutil.copy2(source, working_sdf)
        chunks_base = temp / "export.sql"
        sql_temp = temp / "analysis.sqlite"
        temp_folder = temp / "toolbox-temp"
        temp_folder.mkdir()

        command = [
            str(exporter),
            f"Data Source={working_sdf};Temp Path={temp_folder};",
            str(chunks_base),
            "sqlite",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if completed.returncode:
            raise RuntimeError(
                "SQL CE Toolbox export failed\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )

        chunks = list(temp.glob("export*.sql"))
        chunks.sort(
            key=lambda path: (
                int(match.group(1)) if (match := re.search(r"_(\d+)\.sql$", path.name)) else -1
            )
        )
        if not chunks:
            raise RuntimeError("Exporter completed without numbered SQL chunks")
        connection = sqlite3.connect(sql_temp)
        try:
            for chunk in chunks:
                sql = chunk.read_text(encoding="utf-8-sig")
                connection.executescript(qualify_indexes(sql))
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        finally:
            connection.close()

        if sha256(source) != source_hash:
            raise RuntimeError(f"Source SDF changed during conversion: {source}")
        shutil.copy2(sql_temp, output)

    print(f"SDF: {source}")
    print(f"SQLite: {output}")
    print(f"Source SHA-256: {source_hash}")
    print(f"SQLite SHA-256: {sha256(output)}")
    print(f"Status: integrity_check=ok; source hash unchanged")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdf", type=Path)
    parser.add_argument("--out", type=Path, help="Output path (default: sibling .sqlite)")
    parser.add_argument(
        "--exporter",
        type=Path,
        default=Path(os.environ.get("USERPROFILE", Path.home())) / "SdfConverter" / "ExportSqlCE40.exe",
    )
    args = parser.parse_args()
    source = args.sdf.expanduser().resolve()
    output = (args.out.expanduser().resolve() if args.out else source.with_suffix(".sqlite"))
    try:
        convert(source, output, args.exporter.expanduser().resolve())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
