# Microvellum AI research workspace

This workspace explores a reviewable workflow for turning kitchen drawings into project data for manual import into Microvellum. The intended direction is a Codex skill backed by deterministic local helpers. The repository remains research and prototype code, not a released exporter.

**Not ready for production:** the user reports that one historical XML draft imported successfully and that its product count, positions, dimensions and settings matched the drawing. This is evidence for that specific file, not validation of other drawings, catalog configurations, or manufacturing output. Further calibration is required before production use.

For an agent-driven workflow, give the agent this repository and your drawing and direct it to [the drawing review skill](skills/microvellum-drawing-review/SKILL.md). It describes the checks and routes to the detailed guides. The skill uses the repository's scripts and must be used with a checkout available; it is not a standalone installer.

The repository includes a derived Kitchen Library V7.1 catalog and its [manifest](data/catalog/manifest.json). It contains a library workbook index and the library's Template project, not the private customer projects. Machine-local paths in indexed workbook relationships were redacted. The catalog is an analysis artifact, not a Microvellum database or a complete copy of every source workbook. Its snapshot's completeness and exact match to the target installation are unverified. The `scripts/mv.py` entry point verifies its fingerprint before using it; no SQL CE installation or SDF conversion is needed for routine drawing review.

## Setup and checks

The prototype runs locally on Python 3.11 or newer using the standard library. PDF rendering additionally requires Poppler's `pdfinfo` and `pdftoppm` executables on `PATH`; PNG input does not. The browser loads PDF.js and Three.js from public CDNs, so those views require an internet connection. Node.js is only required for the JavaScript tests.

On Windows, install [Python](https://www.python.org/downloads/windows/) if `py` is unavailable. For PDF input, download and extract a [Poppler for Windows release](https://github.com/oschwartz10612/poppler-windows/releases), then add its extracted `Library\bin` directory to `PATH` and open a new PowerShell window. This project does not bundle or install those tools. No `pip install` is required for the prototype.

From the repository root on Windows:

```powershell
py --version
py scripts/mv.py check
py scripts/mv.py serve
```

Open `http://127.0.0.1:8000/`. The page requires an agent-prepared model and source drawing; it does not interpret the PDF automatically. Follow the [review guide](poc/README.md) for drawing preparation and the [XML workflow](docs/xml-workflow.md) for the limited experimental export. Keep drawings, models, catalog files and output in a private directory outside the repository.

For an agent working from this checkout, the main sequence is:

1. Run `py scripts/mv.py render 'D:\private\drawing.pdf' --out 'D:\private\rendered'`. Visually inspect all rendered pages and record the evidence.
2. Search the bundled catalog with `py scripts/mv.py catalog search 'search term'`. Prepare a private review model from [the template](examples/review-model.template.json), set `project.catalog_sha256` to `catalog_sha256` from [the catalog manifest](data/catalog/manifest.json), identify uncertainties, and review it with the user.
3. Run `py scripts/mv.py publish 'D:\private\review.json'`, then `py scripts/mv.py serve` to show the local review page.
4. After review and resolution of blocking issues, run `py scripts/mv.py export --model 'D:\private\review.json' --out 'D:\private\xml-draft'`. Read the validation report and communicate omissions and uncertainty with the draft.

These commands do not interpret drawings automatically or prove that the resulting XML will import correctly. The default catalog can be overridden: use `--catalog PATH` with `serve` or `export`, or provide the catalog path before the query with `catalog search`. `py scripts/mv.py --help` lists the entry points; append `--help` to a command for arguments.

For development checks, run:

```powershell
py -m unittest discover -s tests -v
node --test tests/test_poc_edit_state.mjs
```

These use synthetic data; they do not test a Microvellum import. Research notes and database-derived reports are local-only and are excluded from Git. Review `git status --untracked-files=all` before publishing; ignore rules alone cannot guarantee that arbitrary customer information is absent.

An optional real-browser test requires a separately installed Playwright Node package and a Chrome/Chromium executable. Set `MV_PLAYWRIGHT_PATH` to the package directory and `MV_BROWSER_EXECUTABLE` to the browser executable before running the Python test command above. The test is skipped when these variables are absent; neither package is a runtime dependency.

## Local data

Keep factory databases, full SQLite analysis copies, drawings and generated job files private. SQL CE conversion is an optional analysis step; see the [SQL CE guide](investigation/sqlce/README.md). The bundled derived catalog is not a Microvellum database.

## Project map

| Path | Responsibility |
|---|---|
| `src/mv_kitchen/` | Reusable Python helpers for drawing rendering and experimental XML workflow. Keep core logic independent of the browser POC. |
| `poc/` | Local Python review server, model validation and experimental XML draft code. Browser assets are in `poc/static/`. See [POC guide](poc/README.md). |
| `investigation/sqlce/` | SQL CE conversion research, format detection, catalog inspection, workbook indexing and project comparison utilities. |
| `data/local/` | Git-ignored local SDF/SQLite research snapshots and derived catalog. |
| `data/catalog/` | Bundled derived V7.1 catalog and checksum manifest. |
| `tests/` | Synthetic fixtures and root-helper checks. There is no root build or dependency manifest. |
| `docs/` | Workflow and implementation notes. |
| `examples/` | Placeholder review model for an agent to complete using actual drawing evidence. |
| `scripts/` | Workflow entry point and environment checks; no Python package installation is needed for the prototype. |
| Local research notes | Ignored working notes; not required to run the prototype. |
| `excelToXml/`, `mmx-engineering-spec-manager/` | Independent nested reference repositories. Their histories, dependencies and status remain separate. |
| `ton/` | Private customer/project material. |

The workflow has three layers: drawing interpretation and human review in the POC; deterministic reusable helpers under `src/`; and catalog extraction/analysis under `investigation/sqlce/`. The POC is illustrative and is not an importer emulator. Reference repositories are research inputs, not runtime dependencies or compatibility evidence.

## Target and evidence

The reported target is Microvellum **15.6.1308.640**, Toolbox OEM 2019, and a modified Kitchen Library v7.1. The user has selected Kitchen Library V7.1 as the intended product source for future projects. The local SDF at `data/local/MicrovellumFactory.sdf` contains a library record with that name, but its completeness and correspondence to the active production library are unverified. See the [SQL CE workflow](investigation/sqlce/README.md).

Do not write to original/live factory databases. SQL CE exporters may mutate their input, so only use disposable copies. SDF-to-SQLite conversion supports analysis only; SQLite does not become a Microvellum factory database, and conversion success does not validate XML import behavior.

## Run the review POC

From the workspace root run `py poc/server.py`, then open `http://127.0.0.1:8000`. PDF.js and Three.js are loaded from public CDNs. Keep customer drawings, job models, generated XML, and analysis outputs outside tracked source paths. See [POC guide](poc/README.md) for the handoff and local catalog configuration.

## Current status

- Experimental XML workflows check selected model and catalog facts and produce calibration drafts. They are not production validators or importer emulators; the reported import of one historical draft does not validate every export; see [XML workflow](docs/xml-workflow.md).
- The SDF snapshots and their SQLite analysis copies are ignored local data. Their provenance and limitations are documented in the SQL CE research notes; none is asserted to be the complete active catalog.
- The root workspace has no build system or dependency manifest. Do not imply tests ran unless a relevant verification was actually performed. Nested repositories document their own setup and checks.

Read [AGENTS.md](AGENTS.md) before changing project conventions. Keep uncertainty explicit, preserve private customer inputs, and update this map when the architecture materially changes.
