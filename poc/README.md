# Drawing review POC

A simple local review page for an agent-interpreted drawing. The user tells the agent which PDF to read. The agent reads it, uses the research library catalog to propose product matches, records uncertain assumptions and questions in JSON, and publishes the PDF and JSON to this page. The reviewer replies in chat with corrections; the agent updates the JSON and the page refreshes automatically.

The page does not invoke an agent itself, write to an SDF or confirm compatibility with Microvellum. An experimental XML writer exists, but it is not a production exporter. A reviewed visual draft is not manufacturing approval. The Front view lays out all wall elevations, Top shows the full modeled plan, and 3D shows all walls together. These views are schematic and display only products and features included in the JSON. Use `drawing_measurements` for dimensions read from the PDF that cannot yet be assigned to an exact product. The 3D view uses box shapes, not engineering geometry. The review focuses on selecting the exact library item, dimensions, and prompt settings. No per-item material choices are collected; generated products retain the defaults supplied by their selected library product and specification group. The catalog snapshot does not evaluate effective material defaults, so those remain governed by the target Microvellum library. `review.applied_rules` lists the rules and evidence used for the current model.

For new jobs, `review.rules` holds the persistent rulebook snapshot and per-rule evaluations. Use `py scripts/mv.py rules snapshot --model PATH`, then record each evaluation before review; see the root README. The page displays enabled rules, including pending outcomes. The older `review.applied_rules` display remains available for existing models but does not meet the export rule-coverage requirement.

## Start the page

From the workspace root:

```powershell
py poc/server.py
```

Open http://127.0.0.1:8000/. It listens only on localhost. PDF.js and Three.js are loaded from public CDNs for the PDF and 3D views; the drawing and JSON are served locally. The original PDF and JSON links open the files. Keep customer files and generated models out of Git.

The page's **Render model JSON** selector lists JSON files from the active model's folder. POC drawing-review JSONs are selectable when their paired PDF or PNG is beside them and its recorded hash still matches. Normalized product-list JSONs can also be selected when a same-named source PDF/PNG is beside them; these open in schematic preview mode, copying X to the horizontal offset and Z to elevation without claiming those coordinates are calibrated. Width, height and depth can be edited and saved back to the selected normalized JSON; other fields stay unchanged. Selecting a file persists it in `poc/.active-review.json`. Other JSON shapes appear as unavailable with a reason. To use a different folder, start the server with `--models-dir 'D:\path\to\job'`. Restart the server after changing its Python code or model folder.

## Re-render from the source JSON

The JSON is the per-job source of truth for both the review data and the drawing reference. Keep `source.path` in it (absolute, or relative to the JSON file) and keep `source.file` equal to that file's name. The review UI is a shared renderer: changing products, dimensions, notes, measurements or questions in JSON does not require changing `index.html`, `app.js` or the CSS.

After the model has been prepared and its visual review metadata filled in, rebuild/publish its page with one command:

```powershell
py poc/render_review.py 'D:\private\job\kitchen.json'
py poc/server.py
```

The first command renders every source page at 200 DPI, verifies the source and page hashes, validates the model, and points the local review page at that JSON/PDF pair. Add `--dpi 300` if you need a higher resolution render. The second command starts the local server; leave it running and edit the same JSON file to see changes within three seconds. Rerun `render_review.py` when changing the JSON path or source drawing. Its temporary PNGs and manifest are discarded after publication because the browser preview reads the original PDF directly.

For a first job, the PDF still needs to be interpreted into the model fields once. The renderer does not infer cabinets from a PDF. In particular, `source.visual_review.source_sha256` and `inspected_pages` must describe the actual drawing pages that were visually inspected; publishing fails if they are missing or stale. A minimal JSON skeleton alone can be created by the agent, but all fields required by `model_validation.py` still apply.

## Agent handoff

1. Render every page first. For PDF input, the tool creates one PNG per page and a hash manifest in a new private output directory. **The LLM must inspect these PNG images and must not read, parse, or visually review the PDF itself.** If a page image is missing, unreadable, or illegible, rerender at a suitable DPI or request a clearer source; do not fall back to PDF input. PNG input is copied as a single page. It requires Poppler's `pdfinfo` and `pdftoppm` on `PATH` for PDF. Do not place customer data in tracked paths.

   ```powershell
   py src/mv_kitchen/render_drawing.py 'D:\path\drawing.pdf' --out 'D:\private\job-render' --dpi 200
   ```

   Inspect every rendered PNG page **visually**. Zoom or crop the plan, elevation, section, title block and notes as needed. Before marking a dimension unknown, search corresponding plans, elevations, sections, details and notes across all PNG pages. Record the views and pages checked and what you found in `dimension_evidence.<dimension>.search`; only mark it unknown if the search finds no reliable value. Read dimension chains and their endpoints, trace each width, height and depth to the feature it describes, and cross-check related views and totals. Distinguish overall dimensions, fronts, carcasses, doors, benchtops, clearances and openings. When a dimension includes a door or benchtop, derive the cabinet depth only from documented dimensions or a confirmed project-specific rule. Record the source dimension and deduction in the evidence. Do not convert missing or ambiguous numbers into numeric facts. The manifest cannot verify that an agent actually looked at the pages: visual inspection remains an agent responsibility. PNG resolution does not guarantee legibility or correct interpretation.
2. Search the read-only research catalog, disambiguate product records using identities and categories, and inspect the product workbook's prompts before proposing settings:

   ```powershell
   py investigation/sqlce/catalog.py search data/catalog/kitchen-library-v7.1.sqlite '2 Door Upper'
   py investigation/sqlce/inspect_product_prompts.py data/catalog/kitchen-library-v7.1.sqlite RECORD_ID --prompt Adj_Shelf_Qty
   ```

   Record exact candidate IDs and source fingerprint, relevant defined names, formulas, workbook compression/coverage and unresolved dependencies. A named prompt is not proof that an override is accepted. Cached values are not evaluated defaults. Formula-driven global settings, units, hidden prompts, subassemblies, cut-parts and hardware can change the result. Do not infer a machining exception from appearance or invent values. The experimental POC exporter emits only confirmed numeric prompt overrides; import behavior is unverified. Consult `docs/xml-workflow.md`.
3. Create the JSON in a private directory. Include `source.path` (absolute or relative to this JSON) and `source.file` (the matching basename). Copy `examples/review-model.template.json` to the private job directory as a starting point. Every placeholder and visual-only measurement must be replaced or reviewed using the actual drawing. The model needs `project.name`, `project.units`, `project.job_number` (an explicit empty string only when intended), `project.specification_groups`, `room.name`, `source.file`, `source.visual_review.source_sha256`, `source.visual_review.inspected_pages`, `room.walls` (an empty list is valid), `room.products`, `room.drawing_inventory`, and `review.issues`; include `review.visual_self_check` results for `front`, `top`, and `3d`. The self-check must record source pages and a comparison note, or explain why no comparable source view exists. Publishing is blocked until all three checks pass or have that explanation. Any model edit clears the check and requires a new comparison before republishing. The inspected page list must include every manifest page. For each product or feature record `dimension_evidence.width/height/depth` with status `observed`, `derived`, `confirmed` or `unknown`, a description and a page number for observed/derived facts. Any `unknown` dimension also needs `search.views_checked`, `search.source_pages` and a `search.result` explaining the cross-view search. Use `null` for unknown dimensions and a separate positive `visual_dimensions` placeholder solely for the drawing box. Evidence should include the source view and endpoints; use pixel crops or bounding boxes where helpful. Record drawing measurements separately from exact Microvellum prompt values. Cross-check non-null dimensions against evidence and selected product.

Each visually identified entity must appear in `room.drawing_inventory`, with source evidence and an explicit resolution: map it to a model product/feature, exclude it with a reason, or leave it unresolved. Unresolved entities block XML export. This coverage ledger applies to cabinets, parts, appliances, counters, rails, walls, annotations and other visible details; it makes omissions reviewable without pretending every drawing detail belongs in Microvellum XML.

Walls are optional internal drawing geometry for the schematic review. The XML profile never serializes `Walls`, `LinkIDWall`, or a library product named `Wall`; product positions may still be derived from an internal wall axis. Each mapped item needs a unique ID, evidence, dimensions with per-dimension status, and a supported placement. The review tool remains a limited schematic, not a general CAD or arbitrary-PDF renderer. It cannot validate target coordinates, geometry collisions, calculations or manufacturing output.
4. Ask only decision-relevant questions that cannot be answered from the inspected drawing, library and established shop rules. Ask no generic confirmation questions about ordinary product choices. Explain conflicting or unsupported evidence in short, single-topic sentences; use ASD-STE100 principles without claiming formal compliance. Confirm a product or prompt only when its choice is genuinely ambiguous and changes the output. Do not use a historical project SDF as an unseen answer when testing PDF-only inference.
5. Before asking the user to review the page, compare the generated Front, Top and 3D views with the corresponding source views in the PNG page images when present. When a direct source view is absent, check consistency against the available orthographic views and document why a direct comparison is unavailable. Fix any mismatch, repeat the comparison, and record all three results in `review.visual_self_check`. Publishing rejects pending or undocumented checks. Recheck after every model edit.
6. Publish with the verified rendering manifest, or use the one-command wrapper (which creates a temporary manifest and runs the same checks):

   ```powershell
   py poc/publish_review.py --model 'D:\private\draft.json' --source 'D:\path\drawing.pdf' --render-manifest 'D:\private\job-render\manifest.json'
   py poc/render_review.py 'D:\private\draft.json'
   ```

   Publishing checks the source hash, every rendered page hash, inspected-page attestation, field evidence and the supported geometry contract. It cannot establish correct interpretation, prompt evaluation or target compatibility. It writes an ignored manifest with private paths. The local server exposes only the selected drawing and JSON at fixed endpoints. The page refreshes when the model changes and refuses invalid models or changed sources. Republish if the source or model path changes. Ask the user for decision-relevant feedback in chat.
7. Apply corrections with evidence and retain actual user confirmation. The experimental POC exporter below can create a calibration draft from this JSON; it is not a production exporter. The separate `docs/xml-workflow.md` workflow remains independent.

## Experimental XML draft from the POC JSON

`export_experimental_xml.py` maps a complete, reviewed POC JSON to an experimental XML draft and validation manifest. It uses exact catalog record IDs, emits only confirmed numeric prompt overrides, records proposed overrides that it omits, and refuses unresolved drawing inventory or review issues. A positive integer item quantity is preserved on one product entry with one position; Microvellum behavior for quantities above one remains unverified. It emits no wall elements or wall links. Product positions are derived from the internal schematic axis; those coordinates have not been calibrated against the target build.

Required `project.specification_groups` entries identify exact catalog groups and may include `global_prompt_overrides` with explicit `status: "confirmed"`. The exporter verifies each confirmed global prompt name against that group's indexed Global workbook and emits it under the XML `Global` element. Values remain uncalibrated in the target importer. Specify each selected group explicitly. When there is more than one group, identify the group on every product.

Set `project.catalog_sha256` to the SHA-256 of the exact catalog used during review, for example `(Get-FileHash -Algorithm SHA256 'D:\private\catalog.sqlite').Hash.ToLower()`. Export checks that this hash and the reviewed drawing hash still match, and that the recorded inspected pages cover the complete PDF. Each exported product currently needs a wall reference for its schematic position, even though an empty wall list is allowed while preparing a review. Replace the template's placeholder wall coordinates with reviewed project geometry.

```powershell
py poc/export_experimental_xml.py --model 'D:\private\job\review.json' --catalog data/catalog/kitchen-library-v7.1.sqlite --out 'D:\private\job\xml-draft'
```

The output directory must be new. Keep customer JSON, catalog copies, and generated XML outside tracked paths. The exporter checks catalog identities, workbook prompt-name evidence, dimensions, references and drawing inventory, then parses the XML, checks product count and asserts that wall tags/links are absent. A successful run remains `insufficient_evidence`; it does not establish import acceptance, correct drawing, workbook calculation, or manufacturing suitability. Do not use the draft for production handoff or CNC output.


### Marked component labels

For drawings with these user-confirmed codes, interpret P as panel, B as bottom panel, T as toe kick, and S as floating shelf. Every tagged occurrence must have its own drawing-inventory entry and a corresponding library-item candidate in `room.products`. Repeated tags identify separate placed items (for example, the two T1 marks). Preserve the printed tag and the candidate product identity. If no exact library match is established, keep the item visible as unresolved and do not silently omit it. Store the rule in `review.applied_rules` so the review page shows which convention was applied.

## Edit the review in the browser

The selected-item panel stages edits per item. Save is disabled while the selected item's fields match the JSON; after any field changes, Save and Cancel are available. Save writes that item's current draft through the revision-guarded JSON endpoint and returns the panel to a clean state. Cancel discards that item's pending edits without changing the JSON. Switching between items preserves pending drafts until each is saved or cancelled. Leave a dimension blank to mark it unknown. Product choices come from the optional normalized catalog. The selected-item panel focuses on library item, dimensions, drawing evidence, and proposed prompt settings. It does not collect per-item material selections; the selected library product and specification group provide their configured defaults.

Start with a catalog to enable the searchable lists:

```powershell
py poc/server.py --catalog 'D:\path\library-catalog.sqlite'
```

The server opens that catalog read-only. Product search covers all product records tied to the one configured library, including records linked to its `Template` project, and searches names, record/link IDs, and populated entity fields. This matters because the research snapshot's `2 Door Upper` products are project-linked. Duplicate names remain distinct choices by record ID. Without `--catalog`, item edits and JSON saves remain available, while catalog searches show a setup message. Each item save validates the full model, atomically writes the selected JSON, and increments `project.revision`. The browser sends the JSON file hash with each save; if another process edits the file first, the UI reports a conflict and requires a reload. Catalog choices reflect the configured research snapshot and are not evidence of production-library compatibility.

The UI and POC model do not collect or search per-item material candidates. The selected library product and specification group provide their configured defaults. The catalog includes workbook material-library and pointer rows for research, but it does not calculate effective defaults; those remain governed by the target Microvellum library.
