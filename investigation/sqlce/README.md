# SQL CE and SQLite investigation helpers

This folder contains research helpers for inspecting SQL Server Compact (`.sdf`) files and SQLite analysis copies. They do not implement Microvellum project import, calculate workbooks, or prove compatibility with a Microvellum build. Verify every new file independently.

## Detect an SDF format version

`detect_sdf_version.py` reads the SDF header using the third-party `sqlce` Python package. It does not read table contents or modify the file.

```powershell
py -m pip install sqlce
py investigation/sqlce/detect_sdf_version.py "D:\path\to\library.sdf"
```

The `data/local/MicrovellumFactory.sdf` reports SQL Server Compact 4.0. The package version is not pinned by a repository dependency file; record it when repeating an analysis.

## Export SDF to a SQLite analysis copy

The tested route used ErikEJ's SQL CE Toolbox command-line exporter `ExportSqlCE40.exe` (release build 3.5.2.99) for the root SQL CE 4.0 snapshot. Download the command-line tools separately from [SqlCeToolbox releases](https://github.com/ErikEJ/SqlCeToolbox/releases); the exporter is not bundled here.

The exporter requires a writable connection for initialization and modified the SDF copy it opened. Use a disposable copy, never the source SDF or a live factory database. After extracting `ExportSqlCE40.exe` into `%USERPROFILE%\SdfConverter`, for example:

```powershell
$out = Join-Path $env:USERPROFILE 'SdfConverter'
New-Item -ItemType Directory -Force -Path (Join-Path $out 'temp') | Out-Null
Copy-Item -LiteralPath 'D:\path\to\library.sdf' -Destination (Join-Path $out 'library-working-copy.sdf')
Get-FileHash -Algorithm SHA256 'D:\path\to\library.sdf', (Join-Path $out 'library-working-copy.sdf')
& (Join-Path $out 'ExportSqlCE40.exe') "Data Source=$out\library-working-copy.sdf;Temp Path=$out\temp;" (Join-Path $out 'library.sql') sqlite
```

The exporter can create one SQL file or numbered scripts such as `library_0000.sql` through `library_0016.sql`, depending on snapshot size. The conversion helper imports either form in order with Python's built-in `sqlite3` module. In the observed snapshot, SQL CE repeated index names across tables; SQLite requires globally unique index names. The import qualified names with table names (for example `idxLinkID__Products`) while preserving indexed columns. This is a SQLite adaptation, not a Microvellum rule.

Example: `py investigation/sqlce/convert_sdf_to_sqlite.py 'D:\path\to\library.sdf'`. The default output is a sibling `.sqlite`; pass `--out` to choose another ignored location.

`investigation/sqlce/convert_sdf_to_sqlite.py` is the reusable SQL CE 4.0 conversion helper. It accepts an SDF and optional output path, uses the Toolbox exporter on a temporary SDF copy, imports the ordered SQL chunks, qualifies table-scoped duplicate index names for SQLite, checks SQLite integrity and confirms the source hash is unchanged. It refuses to overwrite an existing output. `inspect_sqlite.py` inventories the result; there is no SQLite-to-SDF writer.

## Inspect a SQLite database

`inspect_sqlite.py` uses only Python's standard library and opens the database read-only. It reports tables, columns, row counts, indexes, declared foreign keys, sample rows with BLOB summaries, and heuristic `LinkID` relationships based on names and value overlap. Inferred links are candidates, not guaranteed constraints.

```powershell
py investigation/sqlce/inspect_sqlite.py "C:\path\to\library.sqlite" --samples 2 --out "C:\path\to\library-report.md" --json "C:\path\to\library-report.json"
```

## Analysis copies

A converted SQLite file is an analysis copy only. Verify its provenance and correspondence to the active installation independently. Conversion does not establish import compatibility.

## Research catalog and JSON queries

The Git-ignored `data/local/library-catalog.sqlite` is a derived search catalog, not an SDF conversion or a Microvellum database. It contains source fields plus compressed workbook index records. Search is literal case-insensitive substring matching, not semantic or dimensional selection. Use the returned record ID for an unambiguous lookup:

```powershell
python investigation/sqlce/catalog.py search data/local/library-catalog.sqlite sink --limit 10
python investigation/sqlce/catalog.py search-prompts data/local/library-catalog.sqlite Adj_Shelf_Qty --limit 10
python investigation/sqlce/catalog.py product data/local/library-catalog.sqlite RECORD_ID
```

The product response includes raw source fields, candidate category, specification and subassembly links, workbook metadata, and prompt rows with simple defined-name references. A cache is not a recalculated value and matching links are not a complete dependency graph. Prompt results are not verified XML overrides. Fields, units, formulas and dependencies not understood must remain unknown.

To build for a fresh snapshot, run `python investigation/sqlce/index_workbooks.py SOURCE.sqlite INDEX.jsonl`, then run `python investigation/sqlce/catalog.py build SOURCE.sqlite INDEX.jsonl NEW_CATALOG.sqlite`. The builder checks the source fingerprint, checks each indexed workbook's original BLOB hash, checks record counts and refuses to overwrite an existing file. The index now also captures rows from Cut Parts, Sheet Stock, Solid Stock, Buy Out, Edgeband Parts, Edge Band, Hardware Parts, and Hardware Library sheets. The catalog retains these rows as research data; the review POC no longer uses them for per-item material selection. Keep generated catalogs and indices outside tracked paths; the specific local catalog name is ignored. Existing catalogs do not auto-refresh when sources change; rebuild with a new output path after changing index coverage.

## Prompt inspection for a proposed product

`inspect_product_prompts.py` reads a catalog product record by record ID and returns its indexed prompt names, labels, formulas and cached values. `--prompt NAME` checks whether a name maps uniquely, is missing, or is ambiguous; this is not approval to override it. The output retains incomplete-stream status. It does not resolve formulas, units, dynamic constraints or XML encoding. For example: `py investigation/sqlce/inspect_product_prompts.py data/local/library-catalog.sqlite RECORD_ID --prompt Adj_Shelf_Qty`.

## Compare a generated XML file with a project snapshot

`project_to_model.py` creates a normalized JSON reference from a **SQLite analysis copy** of an exported SDF project. It resolves each instance by exact product name plus category against the research catalog. It includes static numeric prompt inputs and numeric cached values from formula-backed exposed prompt cells when they differ from that candidate template. The optional `--include-literal-text` flag also includes nonempty literal text prompt candidates for calibration; their target import behavior is unverified. Formula-cache values can freeze calculated outputs, so their formulas are preserved in the provenance report and their necessity remains unverified. Unreadable or unsupported prompt differences and invalid quantities are recorded; they block the core XML export. Positive integer quantities are preserved on one product entry; importer behavior above one remains unverified. It does not convert or write an SDF.

`compare_project_xml.py` independently checks the XML project/location, all product item numbers and exact names, quantity, dimensions, origin, angle, location and specification group, product prompt candidates, and project Global prompt candidates. It also enforces the no-wall profile. A `pass` means only that those enumerated comparisons match. The report explicitly lists omitted SDF facts, including full workbook blobs/formulas, materials, subassemblies, IDs and other product metadata. It is not XML-import validation or full SDF parity.

Example (keep outputs private and outside tracked paths):

```powershell
py investigation/sqlce/project_to_model.py --project-sqlite 'C:\analysis\canonical.sqlite' --catalog data/local/library-catalog.sqlite --project 'EXACT_PROJECT_NAME' --out 'C:\private\canonical-model.json'
py src/mv_kitchen/xml_workflow.py export --model 'C:\private\canonical-model.json' --catalog data/local/library-catalog.sqlite --experimental --out 'C:\private\xml-draft'
py investigation/sqlce/compare_project_xml.py --sqlite 'C:\analysis\canonical.sqlite' --catalog data/local/library-catalog.sqlite --project 'EXACT_PROJECT_NAME' --xml 'C:\private\xml-draft\project.xml' --out 'C:\private\xml-draft\comparison.json'
```
