"""Read-only inventory and sample-data report for a SQLite database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def display_value(value: object, limit: int = 140) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        preview = raw[:16].hex()
        suffix = "…" if len(raw) > 16 else ""
        return f"<BLOB {len(raw):,} bytes; hex={preview}{suffix}>"
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def table_stem(name: str) -> str:
    stem = name.lower()
    if stem.endswith("files"):
        return stem[:-5]
    if stem.endswith("ies"):
        return stem[:-3] + "y"
    if stem.endswith("s"):
        return stem[:-1]
    return stem


def inferred_link_candidates(conn: sqlite3.Connection, tables: list[str]) -> list[dict]:
    """Find convention-based LinkID references with matching stored values.

    These are candidates inferred from column/table names and value overlap,
    not declared or guaranteed foreign-key constraints.
    """
    by_stem = {table_stem(table): table for table in tables}
    candidates = []
    for source in tables:
        source_columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quote_identifier(source)})")]
        for column in source_columns:
            lower = column.lower()
            if not lower.startswith("linkid") or lower == "linkid":
                continue
            target_stem = lower[len("linkid"):]
            if target_stem.endswith("filename"):
                target_stem = target_stem[:-8]
            target = by_stem.get(target_stem)
            if target is None or target == source:
                continue
            qs, qc, qt = quote_identifier(source), quote_identifier(column), quote_identifier(target)
            target_columns = [row[1] for row in conn.execute(f"PRAGMA table_info({qt})")]
            target_key = next((c for c in target_columns if c.lower() == "linkid"), None)
            if target_key is None:
                continue
            qk = quote_identifier(target_key)
            total_rows = conn.execute(f"SELECT COUNT(*) FROM {qs} WHERE {qc} IS NOT NULL").fetchone()[0]
            if not total_rows:
                continue
            matched_rows = conn.execute(
                f"SELECT COUNT(*) FROM {qs} AS s WHERE s.{qc} IS NOT NULL "
                f"AND EXISTS (SELECT 1 FROM {qt} AS t WHERE t.{qk}=s.{qc})"
            ).fetchone()[0]
            distinct_values = conn.execute(
                f"SELECT COUNT(DISTINCT {qc}) FROM {qs} WHERE {qc} IS NOT NULL"
            ).fetchone()[0]
            matched_values = conn.execute(
                f"SELECT COUNT(DISTINCT s.{qc}) FROM {qs} AS s "
                f"WHERE s.{qc} IS NOT NULL AND EXISTS "
                f"(SELECT 1 FROM {qt} AS t WHERE t.{qk}=s.{qc})"
            ).fetchone()[0]
            candidates.append(
                {
                    "source_table": source,
                    "source_column": column,
                    "target_table": target,
                    "target_column": target_key,
                    "matched_rows": matched_rows,
                    "source_rows": total_rows,
                    "matched_values": matched_values,
                    "distinct_values": distinct_values,
                }
            )
    return candidates


def inspect_database(path: Path, sample_rows: int) -> dict:
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        database_list = [dict(row) for row in conn.execute("PRAGMA database_list")]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        views = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
            )
        ]
        link_candidates = inferred_link_candidates(conn, tables)
        table_reports = []
        for table in tables:
            qt = quote_identifier(table)
            columns = [dict(row) for row in conn.execute(f"PRAGMA table_info({qt})")]
            indexes = []
            for index_row in conn.execute(f"PRAGMA index_list({qt})"):
                index = dict(index_row)
                index["columns"] = [
                    row[2]
                    for row in conn.execute(
                        f"PRAGMA index_info({quote_identifier(index['name'])})"
                    )
                ]
                indexes.append(index)
            foreign_keys = [
                dict(row) for row in conn.execute(f"PRAGMA foreign_key_list({qt})")
            ]
            count = conn.execute(f"SELECT COUNT(*) FROM {qt}").fetchone()[0]
            sample = []
            if sample_rows:
                cursor = conn.execute(f"SELECT * FROM {qt} LIMIT ?", (sample_rows,))
                for row in cursor:
                    sample.append(
                        {key: display_value(row[key]) for key in row.keys()}
                    )
            table_reports.append(
                {
                    "name": table,
                    "row_count": count,
                    "columns": columns,
                    "indexes": indexes,
                    "foreign_keys": foreign_keys,
                    "samples": sample,
                }
            )

        return {
            "database": str(path.resolve()),
            "sqlite_version": sqlite3.sqlite_version,
            "database_list": database_list,
            "table_count": len(tables),
            "view_names": views,
            "inferred_link_candidates": link_candidates,
            "tables": table_reports,
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_check": [
                tuple(row) for row in conn.execute("PRAGMA foreign_key_check")
            ],
        }
    finally:
        conn.close()


def markdown_report(report: dict) -> str:
    lines = [
        f"# SQLite inventory: `{report['database']}`",
        "",
        f"- SQLite runtime: {report['sqlite_version']}",
        f"- Tables: {report['table_count']}",
        f"- Views: {len(report['view_names'])}",
        f"- Integrity check: **{report['integrity_check']}**",
        f"- Declared foreign-key violations: {len(report['foreign_key_check'])}",
        "",
        "## Table summary",
        "",
        "| Table | Rows | Columns | Indexes | Declared FKs |",
        "|---|---:|---:|---:|---:|",
    ]
    for table in report["tables"]:
        lines.append(
            f"| `{table['name']}` | {table['row_count']:,} | {len(table['columns'])} | "
            f"{len(table['indexes'])} | {len(table['foreign_keys'])} |"
        )
    lines.extend(["", "## Table details", ""])
    lines.extend(
        [
            "## Inferred LinkID relationships",
            "",
            "Candidates below are inferred from a `LinkID...` column name, a matching target table name, "
            "and actual value overlap. SQLite does not declare these as foreign keys.",
            "",
            "| Source column | Target column | Matching rows | Distinct values matched |",
            "|---|---|---:|---:|",
        ]
    )
    for link in report["inferred_link_candidates"]:
        lines.append(
            f"| `{link['source_table']}.{link['source_column']}` | "
            f"`{link['target_table']}.{link['target_column']}` | "
            f"{link['matched_rows']:,}/{link['source_rows']:,} | "
            f"{link['matched_values']:,}/{link['distinct_values']:,} |"
        )
    if not report["inferred_link_candidates"]:
        lines.append("| None found | — | — | — |")
    lines.append("")
    for table in report["tables"]:
        lines.extend(
            [
                f"### `{table['name']}`",
                "",
                f"Rows: {table['row_count']:,}",
                "",
                "Columns (`name`: type; PK = primary key; NN = not null):",
            ]
        )
        for column in table["columns"]:
            flags = []
            if column["pk"]:
                flags.append(f"PK {column['pk']}")
            if column["notnull"]:
                flags.append("NN")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            default = f" DEFAULT {column['dflt_value']}" if column["dflt_value"] is not None else ""
            lines.append(
                f"- `{column['name']}`: `{column['type'] or 'unspecified'}`{flag_text}{default}"
            )
        if table["indexes"]:
            lines.extend(["", "Indexes:"])
            for index in table["indexes"]:
                unique = " UNIQUE" if index["unique"] else ""
                lines.append(
                    f"- `{index['name']}`{unique}: "
                    f"{', '.join('`'+str(c)+'`' for c in index['columns'])}"
                )
        if table["foreign_keys"]:
            lines.extend(["", "Declared foreign keys:"])
            for fk in table["foreign_keys"]:
                lines.append(
                    f"- `{fk['from']}` → `{fk['table']}.{fk['to']}` "
                    f"(on update {fk['on_update']}; on delete {fk['on_delete']})"
                )
        if table["samples"]:
            lines.extend(["", "Sample rows (values truncated; BLOBs summarized):", ""])
            for number, sample in enumerate(table["samples"], 1):
                rendered = json.dumps(sample, ensure_ascii=False, separators=(", ", ": "))
                lines.append(f"{number}. `{rendered}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect SQLite tables, schema, indexes, foreign keys, and sample rows (read-only)."
    )
    parser.add_argument("database", type=Path, help="Path to the SQLite database")
    parser.add_argument("--samples", type=int, default=2, help="Sample rows per table (default: 2; 0 disables)")
    parser.add_argument("--out", type=Path, help="Write Markdown report to this path instead of stdout")
    parser.add_argument("--json", dest="json_out", type=Path, help="Also write the raw inspection data as JSON")
    args = parser.parse_args()
    db_path = args.database.expanduser().resolve()
    if not db_path.is_file():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        return 2
    if args.samples < 0:
        parser.error("--samples must be zero or greater")
    try:
        report = inspect_database(db_path, args.samples)
        markdown = markdown_report(report)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(markdown, encoding="utf-8")
            print(f"Markdown report written to {args.out.resolve()}")
        else:
            print(markdown)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            print(f"JSON report written to {args.json_out.resolve()}")
        return 0
    except sqlite3.Error as exc:
        print(f"SQLite inspection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
