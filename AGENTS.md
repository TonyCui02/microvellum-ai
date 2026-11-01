# Repository guidance

## Purpose and scope

This workspace researches and prototypes a workflow that turns kitchen drawings into reviewed project data for manual import into Microvellum. The intended direction is a Codex skill with deterministic local helpers. The current repository is a research workspace, not a released exporter.

## Read before changing the project

- `README.md` describes the workspace and its components.
- When present locally, `plan.md` is the proposed design and implementation sequence; `research.md` and `findings.md` contain supporting investigation notes. These local research files are not distributed in the repository.
- `investigation/sqlce/README.md` records the tested SDF/SQLite workflow and its limits; read it before repeating or extending conversion work.
- `poc/README.md` documents the standalone browser review prototype.
- For a drawing-to-XML request, follow `skills/microvellum-drawing-review/SKILL.md` and the guides it references.
- If present locally, `excelToXml/` and `mmx-engineering-spec-manager/` are separate nested repositories with their own READMEs and Git history. Treat them as reference projects unless a task explicitly asks to modify them. They are not included in a clean checkout.

## Accuracy and safety

- Distinguish observed, documented, proposed, and unverified behavior in documentation.
- Do not describe sample XML, the experimental SDF, the POC, or nested repository tests as proof of compatibility with the target Microvellum build.
- The supplied target is Microvellum 15.6.1308.640 / Toolbox OEM 2019 / modified Kitchen Library v7.1. The local SDF is a research snapshot; its library label does not prove that it is the complete/current production catalog. Do not use its contents as production defaults without matching provenance.
- `MicrovellumFactory.sdf` under `data/local/` has now been identified as SQL Server Compact 4.0 and contains a library named `Kitchen Library V7.1` plus a `Template` project. Its completeness and exact correspondence to the target workstation remain unverified; do not promote its values to production defaults.
- SQL CE Toolbox's 4.0 exporter requires a writable connection and changed its working SDF copy during initialization. Always export from a disposable copy, preserve and compare the original hash, and keep SDF/SQLite/SQL outputs outside tracked source paths. Its SQLite export was emitted as ordered numbered SQL chunks; this snapshot required table-qualified index renaming before SQLite could execute all chunks.
- `investigation/sqlce/detect_sdf_version.py` uses the optional `sqlce` Python package to read the SDF header. `inspect_sqlite.py` uses only the Python standard library and opens SQLite in read-only mode. The Toolbox binary is a separate download and is not a committed dependency.
- Keep customer drawings, project files, factory databases, and extracted/vendor binaries out of commits unless explicitly requested and reviewed for sharing.
- Do not automate Microvellum UI, modify original/live factory databases, or generate CNC output as part of this project direction. Some SQL CE exporters write to their input while opening it; use only a disposable copy.
- Keep `poc/` illustrative and separate from production import/export claims.
- Treat SDF-to-SQLite as an analysis-copy workflow only. SQLite does not become a Microvellum factory database, and conversion success does not validate XML import behavior.

## SDF to SQLite mappings

In the original local research workspace, the following SDF files have SQLite analysis copies. These private inputs are not included in a clean checkout. When present, use the SQLite path for local read-only queries:

| SDF | SQLite analysis copy |
|---|---|
| `data/local/MicrovellumFactory.sdf` | `data/local/MicrovellumFactory.sqlite` |
| `investigation/sqlce/sample-copy.sdf` | `investigation/sqlce/sample-copy.sqlite` |
| `mmx-engineering-spec-manager/example_data/microvellum/sdf/MicrovellumExport.sdf` | `data/local/reference-MicrovellumExport.sqlite` |
| `ton/MicrovellumFactory.sdf` | `ton/MicrovellumFactory.sqlite` |
| `ton/New folder/MicrovellumFactory.sdf` | `ton/New folder/MicrovellumFactory.sqlite` |
| `ton/New folder/MicrovellumFactoryOutput.sdf` | `ton/New folder/MicrovellumFactoryOutput.sqlite` |

The SDFs and SQLite copies are ignored local research/customer data. The `ton/` snapshots are private project inputs. Do not commit them or use them as generic fixtures. The nested reference repository's SQLite copy is deliberately placed in root `data/local/` to preserve its independent Git status.

When adding an SDF, convert it and add its source/output pair to this table. For SQL CE 4.0, use `py investigation/sqlce/convert_sdf_to_sqlite.py path\to\source.sdf`. It creates a sibling `.sqlite`, refuses to overwrite an existing output, exports only from a temporary working copy, qualifies duplicate SQL CE index names for SQLite, checks `PRAGMA integrity_check`, and verifies the source hash did not change. For these mappings and known limits, see [SQL CE README](investigation/sqlce/README.md). Query analysis copies in read-only mode, for example `py investigation/sqlce/inspect_sqlite.py "data/local/MicrovellumFactory.sqlite"` or Python `sqlite3.connect("file:path.sqlite?mode=ro", uri=True)`.

## Changes

- Make focused changes in the root workspace unless a task names a nested repository.
- Preserve each nested repository's independent status and conventions.
- There is no root build or test harness currently. Do not imply the project has been tested unless a relevant verification was actually run.
