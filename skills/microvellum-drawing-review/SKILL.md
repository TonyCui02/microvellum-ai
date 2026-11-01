---
name: microvellum-drawing-review
description: Interpret a kitchen or cabinetry PDF, review its contents with the user, and prepare an experimental Microvellum XML draft using this repository's local tools.
---

# Drawing review and XML draft

Work from the root of the microvellum-ai checkout. Read `README.md`, `poc/README.md`, and `docs/xml-workflow.md` before beginning; those documents describe dependencies, model fields, commands, and current limitations. Keep the PDF, catalog, working model, rendered pages, and output in a private job directory outside the checkout.

1. Obtain the drawing. `scripts/mv.py` uses the bundled Kitchen Library V7.1 catalog by default and verifies its manifest checksum; use it for catalog search, prompt inspection, review and export. Read `data/catalog/manifest.json` and record its fingerprint. Its snapshot's completeness and match to the active installation are unverified; do not invent product identities. Use a different catalog only when the shop supplies one and its provenance is understood. Use SQL CE analysis only if needed and never open a live SDF with a writable exporter.
2. Render every PDF page with `py scripts/mv.py render`. Inspect the full pages and relevant details visually. Record source evidence, units, dimensions, uncertainty, and all visible entities in the drawing inventory as described in the review guide. Do not infer missing dimensions as facts or rely on unconfirmed shop defaults.
3. Resolve exact catalog product and group identities and inspect relevant prompts. Present the proposed layout, schedule, assumptions, unresolved items, and material omissions. Ask focused questions where ambiguity affects the result. Use the local review page when useful; it is schematic and does not interpret drawings itself.
4. Record actual user review and corrections. Export with `py scripts/mv.py export` only when its validation requirements are met. Inspect the resulting validation report and tell the user which requested details were omitted or remain unverified. If requirements cannot be met, report the blocker rather than fabricating an approved model or XML.
5. Deliver the draft and its validation report for manual evaluation. One specific historical XML was reported successfully imported and checked against its drawing, but this does not establish that new exports produce correct geometry, settings, materials, or manufacturing output. Do not promise production readiness, automate Microvellum, modify factory databases, or generate CNC output.

For deeper investigation, the bundled catalog is SQLite: open `data/catalog/kitchen-library-v7.1.sqlite` with `sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)`. Its `entities`, `workbooks` and `prompt_names` tables contain indexed data; workbook `payload` values are zlib-compressed JSON. Machine-local relationship targets are redacted, and raw workbooks and source SDF tables outside the index are not bundled. State when an investigation requires evidence the index does not contain.
