# Local and generated data

`catalog/` contains the bundled derived Kitchen Library V7.1 catalog and its checksum manifest. It contains indexed library metadata and the library's Template project, but not the original SDF or the private customer-project snapshots. Machine-local paths in indexed workbook relationships are redacted. Raw workbooks and source tables outside the index are not included. The source snapshot's completeness and correspondence to the active workstation remain unverified. The catalog is an analysis artifact, not a Microvellum database.

`local/` holds machine-local research inputs and generated analysis copies, including SQL CE snapshots and SQLite exports. Its contents are excluded from Git because they may be large, provenance-sensitive, or contain factory/project data. Recreate or replace them only through the documented workflows in `../investigation/sqlce/README.md`.

Customer drawings, project SDFs, rendered pages, workbook payloads, exporter binaries, and temporary audit manifests belong in a private working directory outside this repository. The ignored `ton/` folder is legacy private project material; do not add more customer data there.
