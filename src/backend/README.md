# Backend checkpoints

## Knowledge graph checkpoint 2 - local entity suggestions

`services/entity_mentions.py` runs GLiNER2 locally on exact source passages. The `research-v1` schema covers person, organization, project, model, method, dataset, and component; `full-v1` (the default) adds date, event, and location. It uses `fastino/gliner2-base-v1`, pinned to revision `f9634218e53580c56edf0de97ca1a7d3f1c2354e`, and `gliner2==2.1.0`. The optional dependency lock preserves the OCR checkpoint versions. Our installed PyTorch is CPU-only and this checkpoint explicitly uses CPU inference.

```powershell
uv pip install --python .venv/Scripts/python.exe -r src/backend/requirements-entities.txt
.\.venv\Scripts\python.exe -m src.backend.services.entity_mentions .cache/attention-ocr-v2.json --output .cache/attention-entities-research-v1.json
```

The input is a saved `LayoutReport`, not the paginated passage response. First use downloads model files from Hugging Face; source text stays on the machine. Cached weights support subsequent local inference. Choose a new output filename; existing reports are never overwritten. The CLI writes local JSON. The API and review screen described in checkpoint 3 below store and review the same suggestions per user.

GLiNER2 scans overlapping 128-word windows with 32-word overlap and batch size one. Window offsets map back to unchanged passage text. This avoids simply truncating longer passages, although context across passage boundaries remains unavailable. Each candidate must have a supported label, finite score at or above the configurable threshold (default `0.5`), valid character offsets, and text exactly matching both the passage slice and original extraction citation. Invalid candidates are counted and rejected; malformed responses and inference failures are recorded separately from successful empty results. Per-passage status and warnings make partial failures visible. The CLI saves partial reports but exits nonzero if any passage failed.

All accepted outputs remain **suggestions**. Model scores are not calibrated probabilities. Stable mention IDs include passage, span, label, model revision, library version, label schema, threshold, and window configuration. Reports retain document/run IDs and source hash. OCR and other source warnings remain attached. Repeated mentions stay separate; there is no identity merging, relationship inference, automatic acceptance, or correction of original text.

Run deterministic validation tests normally. For an opt-in real-model check over eight annotated synthetic notes:

```powershell
$env:DIGITAL_MEMORY_RUN_ENTITY_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/backend/test_entity_mentions_live.py -q -s
Remove-Item Env:DIGITAL_MEMORY_RUN_ENTITY_TESTS
```

The evaluation writes exact-span-and-label precision/recall and missed/extra spans to `.cache/entity-synthetic-evaluation.json`, plus cited suggestions to `.cache/entity-synthetic-mentions.json`. It checks every produced citation, reports accuracy without inventing a benchmark threshold, and includes names, organizations, named projects, ambiguous fruit/company wording, generic tasks, Unicode, and line breaks. This small development set does not establish general accuracy or satisfy the eventual 20-run product assessment. Direct note ingestion still needs a document-level citation adapter; these synthetic notes use PDF-style extraction records only as test inputs.

The initial real-model evaluation returned 15 suggestions for 15 annotated mentions: 13 matched exact spans and labels, giving 86.7% precision and recall. Two boundary differences accounted for the errors: `Horizon` versus `Project Horizon`, and inclusion of `Dr.` in `Ayesha Malik`. Every returned citation resolved exactly. These results are a development observation, not a claim of production readiness.

The Attention paper also exposes a domain limitation: the project label incorrectly includes generic terms such as `encoder` and `decoder`, sometimes with high scores. Raising the score threshold alone would not solve this. Project suggestions require explicit review and a broader annotated evaluation before graph promotion; this checkpoint never promotes any mention automatically. Citation correctness verifies the quoted evidence, not the correctness of the entity label.

The saved paper run, `.cache/attention-entities-v2.json`, contains 479 suggested mentions across 93 passages: 204 person, 133 organization, and 142 project predictions. Four invalid candidates were rejected, zero passages failed, and all 479 remaining citations were independently resolved against the original native/OCR extraction strings. Counts include repeated occurrences and OCR/native duplicates; they are not unique graph entities or verified label totals.

### Project-label evaluation

`tests/fixtures/project_labels.json` contains 40 authored synthetic notes split into 20 development and 20 held-out evaluation cases. Each split includes ten cases without a named project. Annotations specify exact name boundaries, include `Project` or `Operation` when part of a name, and cover repeated occurrences. Technical components, model names, organizations, generic tasks, and ambiguous references have empty project annotations. These annotations were authored for development and still benefit from independent human review.

`tests/backend/evaluate_project_labels.py` compares the original description with two clearer descriptions on development cases. It selects by exact-span F1, then precision, then configuration order. The evaluation phase reads that saved selection and compares it with the baseline on held-out cases. Model revision, threshold `0.5`, window settings, and person/organization descriptions stay fixed across experiments. Every emitted citation is checked. Reports include individual false positives, missed spans, inference failures, and counts for the other labels.

```powershell
.\.venv\Scripts\python.exe tests/backend/evaluate_project_labels.py --phase development --output .cache/project-label-development-v1.json
.\.venv\Scripts\python.exe tests/backend/evaluate_project_labels.py --phase evaluation --development-report .cache/project-label-development-v1.json --output .cache/project-label-evaluation-v1.json
```

Choose new output filenames for repeat runs. The extractor accepts a complete `labels` description mapping for reproducible experiments and records it in the report and mention identities.

Results recorded on November 16, 2026 with the pinned model and library follow.

| Split | Description | Correct / predicted / expected | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| Development | Original | 12 / 20 / 13 | 60.0% | 92.3% | 72.7% |
| Development | Explicit project definition | 13 / 21 / 13 | 61.9% | 100.0% | 76.5% |
| Development | Named initiative definition | 13 / 19 / 13 | 68.4% | 100.0% | 81.3% |
| Held-out | Original | 12 / 21 / 12 | 57.1% | 100.0% | 72.7% |
| Held-out | Selected named initiative definition | 10 / 17 / 12 | 58.8% | 83.3% | 69.0% |

The original project description was retained after this experiment because the selected candidate reduced held-out F1. The candidate reduced extra suggestions from nine to seven but missed `Silverline` and `Evergreen`. Both configurations still incorrectly classify `Encoder` and `Decoder` as projects. Clearer descriptions alone produced insufficient generalization in this experiment. The eight-note, all-label evaluation above measures a different dataset and should be interpreted separately.

### Expanded research schema

The next schema experiment kept the original three descriptions and added four categories. A model is a named architecture such as BERT or Transformer. A method is a computational technique such as beam search or dropout. A dataset is a named training or evaluation collection such as ImageNet. A component is an architecture part such as encoder, decoder, or attention head. Component and method mentions can be generic technical concepts, so downstream graph creation still requires review and identity resolution.

`tests/fixtures/entity_schema_evaluation.json` contains 24 fresh synthetic examples with annotations for all seven types, authored before comparing the frozen schemas. Examples include personal notes, research passages, mixed project/research text, repeated names, Unicode, and ambiguous references. The exact-span metrics check labels and offsets together. Shared-label metrics compare person, organization, and project under both configurations. The all-label recall for the core schema also includes the research annotations it cannot emit.

```powershell
.\.venv\Scripts\python.exe tests/backend/evaluate_entity_schemas.py --output .cache/entity-schema-evaluation-v1.json
```

The report records the fixture SHA-256, complete schemas, pinned model revision, library version, threshold, window settings, per-label results, and individual extra/missed spans. Every returned citation is resolved against its original extraction. Results from November 16, 2026 follow.

| Metric on 24 examples | Core three types | Expanded seven types |
| --- | --- | --- |
| Project exact-span precision | 45.0% | 72.7% |
| Project exact-span recall | 100.0% | 88.9% |
| Project false positives | 11 | 3 |
| Project missed mentions | 0 | 1 |
| Shared-label F1 | 60.0% | 77.8% |

The expanded schema produced 39 correct exact-span-and-label matches from 47 predictions against 42 annotations, giving 83.0% precision and 92.9% recall across all seven types. All ten component annotations matched. Dataset recall was 50% because SQuAD was twice assigned an organization label. Project errors included extra predictions for BERT and ResNet and an incomplete `Project Snowdrop` name. These small authored examples still need independent annotation review and broader evaluation before automatic graph promotion.

`full-v1` (see checkpoint 3) is now the default for both the Python extractor and CLI; `research-v1` remains available with `--schema research-v1`. Use `--schema core-v1` or pass `labels=CORE_LABELS` for the original configuration. Reports record `label_schema_version`; older reports without that field load as `legacy`. Mention IDs continue to include the complete label configuration, and rebuilding core results preserves the existing identity inputs. If the same exact span receives multiple types, each suggestion and its passage carry an explicit review warning. Every mention retains its source citation and suggested status.

The original eight-note evaluation with the broader default yielded 14 exact matches from 16 predictions against 15 annotations, giving 87.5% precision and 93.3% recall. Remaining errors included the organization label for the fruit word `apple` and inclusion of `Dr.` in a person's name.

The updated paper artifact, `.cache/attention-entities-research-v1.json`, contains 716 suggestions across 93 passages with zero failed passages. All 716 citations were independently resolved against the original extraction. Counts are 204 person, 93 organization, 37 project, 106 model, 153 method, 53 dataset, and 70 component predictions. Four invalid candidates were rejected and 25 passages carry conflicting-type warnings. Exact `encoder`/`decoder` predictions comprise 41 component labels and one model label. Residual project errors include `Softmax` and diagram fragments, so review remains necessary. The paper has no fully annotated entity ground truth and these counts measure output volume.

## Knowledge graph checkpoint 3 - stored entities and human review

Apply `202611170001_entities.sql` (`python -m src.backend.supabase.migrate`) before using these routes. It adds `entity_runs` (one model pass over one saved extraction) and `entity_mentions` (one row per suggestion, keyed by run and the deterministic mention ID, so re-running never collides). Both tables have row-level security; clients can only `SELECT` their own rows. Writes go through three `security definer` functions that check ownership: `start_entity_run`, `finish_entity_run` (inserts all mentions with the run result atomically), and `review_entity_mention`. A run that stays `processing` for 20 minutes can be superseded. Each mention keeps the exact citation, score, warnings, and source hash. A reviewer's decision is stored beside the model's label: `review_status` (`suggested`, `accepted`, `rejected`) and an optional `reviewed_label`, and the original `label` is never overwritten.

| Route | Purpose |
| --- | --- |
| `POST /documents/{id}/entities?schema=full-v1` | Start a background run on the saved extraction (`core-v1`, `research-v1`, or `full-v1`). Shares the single worker slot with PDF processing. |
| `GET /documents/{id}/entities?status=&label=&min_score=&limit=&offset=` | Latest run, whole-run review counts, and a filtered page of mentions in reading order. |
| `PATCH /documents/{id}/entities/{run_id}/{mention_id}` | Body `{"status": "accepted"\|"rejected"\|"suggested", "label": optional corrected type}`. A corrected type is only allowed when accepting. |

The workspace's document dialog now shows an **Entities** panel for processed documents: choose a type set, find entities, filter by status and type, accept, reject, undo, accept under a different type, and open the exact surrounding source text with the span highlighted.

### Schema expansion

The saved Attention-paper results showed that the research labels had nowhere to put several kinds of text that a timeline needs, so the model forced them into wrong labels (for example `Q3 budget review` as a project and `We` as a person). `full-v1` adds three types: `date`, `event`, and `location`. Authored timeline sentences were corrected as expected (`3 March 2024`, `September 15`, `next Friday` as dates; `Q3 budget review` and `kickoff conference` as events; `Lahore`, `Islamabad`, `Berlin` as locations).

On the 93-passage Attention paper (`.cache/attention-entities-full-v1.json`), `full-v1` produced 819 suggestions against 716 for `research-v1`, with zero failed passages, four rejected invalid candidates, and 819 cited spans. The 103 extra suggestions are 101 dates, 16 events, and 1 location, with small movement in other types. The trade-off is real. Many date hits are legitimate bibliography years and conference events, but BLEU scores such as `28.4` and `25.4` are labelled as dates, and `WMT 2014` moved from dataset to date. Use `research-v1` for papers. Use `full-v1` for notes and anything feeding a timeline. These are unreviewed model outputs, not measured accuracy, and no held-out annotation set exists yet for the three new labels.

### Timeline-label evaluation

`tests/fixtures/timeline_labels.json` holds 41 fresh synthetic notes (85 date/event/location annotations among 116 total, plus deliberate negatives) with the annotation policy stored in the file. It was written before any run and the label descriptions were not tuned against it. Exact span and label must both match.

```powershell
.\.venv\Scripts\python.exe tests/backend/evaluate_timeline_labels.py --output .cache/timeline-label-evaluation-v1.json
```

Results on November 17, 2026 (pinned model, threshold 0.5):

| Exact-span metric | `research-v1` | `full-v1` |
| --- | --- | --- |
| Timeline labels: precision / recall / F1 | no such labels / 0% / 0% | 91.4% / 100% / 95.5% |
| Date: precision / recall | n/a | 92.7% / 100% (38 of 38) |
| Event: precision / recall | n/a | 84.0% / 100% (21 of 21) |
| Location: precision / recall | n/a | 96.3% / 100% (26 of 26) |
| All other labels: precision / recall / F1 | 47.5% / 90.3% / 62.2% | 65.9% / 87.1% / 75.0% |

There were no boundary-only timeline errors and no failed passages. The eight timeline false positives are mostly cases where the policy and the model disagree: clock times and durations tagged as dates (`7:30 pm`, `3.5 days`, `12 hours`), `7:30 pm show` and bare `Dinner` as events, `Karachi` extracted inside a named festival, and the verb-like events `flight` and `Nothing`. The non-timeline labels improved because dates and places no longer get forced into project or organization. They still show weaknesses: `et al.` is included in person spans, `GLUE` and `ImageNet` are sometimes labelled project or organization, and `WMT 2014 English-German` is truncated.

Interpret this cautiously. The set is small, one author wrote both the policy and the annotations, the sentences are short and clean, and no real-world scan or paper text is included. The 100% recall is a ceiling for easy cases, not an expectation for messy documents. A second annotator and longer, noisier passages should come before relying on these numbers. Reviewing suggestions in the entity panel remains required.

### Phase 0 hardening

**Review decisions survive re-runs.** `202611170002_carry_entity_reviews.sql` makes a new run inherit the latest earlier accept/reject/retype decision for the same document, exact source span and model label. A re-extracted document has new region IDs, so a changed source cannot inherit decisions. A mention with a different label stays awaiting review. This was verified against the live database in a transaction that was rolled back.

**Cleanup rules (`entity-mentions-v2`).** `refine_span` removes a trailing `et al.` from person names. It drops clock times (`7:30 pm`), durations (`3.5 days`) and bare decimals (`28.4`) from dates, because they are not calendar dates. Offsets stay exact and citations still resolve. Dropped spans are counted as `filtered_candidate_count`, separately from invalid model output. Passages that look like reference-list entries (a year plus words such as `arXiv`, `Proceedings of`, `pp.`) get a "Looks like a bibliography entry" warning instead of being dropped, and the review panel has a **Hide reference-list matches** filter (`hide_bibliography=true`).

On the Attention paper, v2 produced 784 suggestions (819 before): 35 non-date spans were filtered, no `et al.` remains in a name, and 323 of the 784 (41%, including 164 of 204 person mentions) are flagged as bibliography-like. On the timeline set (`.cache/timeline-label-evaluation-v2.json`), timeline precision rose from 91.4% to 94.4% at 100% recall, and non-timeline F1 from 75.0% to 80.6%. **These rules were written after seeing that set's errors, so the new numbers are no longer held-out and overstate real performance.** Use a second annotator's sheet or fresh notes for an honest figure.

**Second annotator.** `python tests/backend/annotation_agreement.py sheet --output .cache/timeline-sheet.json` writes the same 41 notes with empty annotations and the policy. After someone fills it in without seeing the reference or model output, `python tests/backend/annotation_agreement.py compare .cache/timeline-sheet.json` reports per-label agreement and lists every disagreement, which shows where the policy is ambiguous.

**Live isolation check.** `python -m src.backend.check_isolation DOCUMENT_ID` asks for two users' access tokens (hidden). User A must own an extracted document with an entity run. It confirms A can read their data (so empty results cannot pass vacuously). It confirms B and an unauthenticated caller can read no rows from `documents`, `document_extractions`, `entity_runs` or `entity_mentions`, cannot download the PDF, and cannot call the start/finish/review functions on A's data, and that A's run is unchanged afterwards. Use test accounts and documents only.

Not yet addressed: bibliography text still produces person, organization, and event noise, and the same entity is not merged across mentions. Identity resolution and relationships are the next stage.

## Knowledge graph checkpoint 1 - source passages

`services/source_passages.py` deterministically partitions saved native/OCR region strings into passages for later entity extraction. `schemas/passages.py` defines passage and paginated-response contracts. Each passage has a stable UUID, ordinal, exact text, source SHA-256, native/OCR method, review flags, and a `CitationReference` containing document, extraction run, physical page, region, and character range.

The first algorithm, `source-passages-v1`, uses a 1,200-character maximum. It prefers paragraph boundaries, then sentence-like punctuation, then whitespace, falling back to a hard character boundary for long unbroken text. This is a character limit, not a model token limit or linguistic sentence detector. Passages never cross pages or regions. They have no overlap. Original Unicode, whitespace, line breaks, and hyphenation are retained. Whitespace-only fragments and empty, failed, or unsupported regions are excluded. All non-whitespace characters in eligible regions remain covered. Offsets are zero-based, end-exclusive Python string offsets, matching `ExtractionRun.resolve_citation`.

IDs derive from the extraction UUID, region UUID, algorithm/configuration, and source offsets. Rebuilding from the same saved report produces identical IDs after restart or JSON round trips. Re-extracting a PDF creates new run IDs and therefore new passage IDs. Existing reports work without reprocessing or a database migration. Passages are derived on read from privately persisted reports; a separate passage index/table can be added alongside entity storage later. This endpoint operates on finalized PDF extraction reports. Direct JSON notes need a future document-level source-locator adapter.

`GET /documents/{id}/passages?limit=20&offset=0` requires the same bearer token and ownership checks as the extraction endpoint. It returns extraction IDs, algorithm configuration, total count, skipped-region count, and a slice of passages. Limits are 1-100 with nonnegative offsets. Missing/hidden documents return 404, and unfinished or unavailable saved results retain the extraction endpoint's 409 behavior. It makes no database writes or model calls. Pagination currently reads and partitions the saved report in memory.

Page order is physical PDF order, followed by native and then OCR regions on each page. These regions retain distinct provenance even when strings happen to match. Review flags inherit report, extraction, page, region, layout, and table warnings. OCR text remains reviewable. Native page text that contains flattened tables still needs review; this step does not reinterpret table numbers or repair equations. Structured cell text is not flattened into additional passages. Future entity extraction should use these warnings when producing suggestions.

For local inspection, provide an existing saved report and a new output filename:

```powershell
.\.venv\Scripts\python.exe -m src.backend.services.source_passages .cache/attention-ocr-v2.json --output .cache/attention-source-passages-v1.json
```

The Attention report produced 93 passages (40 native, 53 OCR). Every passage was checked against its exact source slice, with stable regenerated IDs and complete eligible non-whitespace coverage. This verifies passage provenance, not the accuracy of the original OCR or table extraction. Tests cover Unicode, CRLF, long unbroken strings, boundaries, separate regions, skipped failures, warning inheritance, stable IDs, pagination, and authenticated API access.

## Extraction checkpoint 6 - saved results and document status

Migration `202611150001_extraction_status.sql` adds private extraction-attempt records and document processing metadata. Apply with `.\.venv\Scripts\python.exe -m src.backend.supabase.migrate` after configuring the development database. It has been applied and its ownership/permission checks passed on the configured project. Applied migrations remain immutable.

Install `requirements-ocr.txt`, start FastAPI, then open a PDF in the frontend and select **Extract document**. The API verifies ownership and atomically claims an attempt before returning **202 Accepted** with `status: processing` and a status Location header. A FastAPI background task uses a fresh user-scoped HTTP client, downloads the bounded private original, and runs native extraction, layout, tables, and OCR in a thread. The task retains the verified access token in memory only. Processing and result retrieval use no service-role key.

`POST /documents/{id}/process` starts processing. `GET /documents/{id}` returns persisted status. `GET /documents/{id}/extraction` returns the current completed layout report, including versioned native/OCR citations and tables. Lists include status metadata without downloading reports. Missing/hidden documents return 404. Active duplicate attempts or a busy local processor return 409. JSON text documents already have content and do not use PDF extraction.

Results and terminal status are committed together through ownership-checked database functions. Straightforward complete text can become `ready`; uncertain content, OCR review items, tables, and layout failures become `needs_review`. Processing errors become `failed` with a fixed, sanitized message. The raw PDF `content` field remains unchanged; derived text lives in the saved report. Ready means extraction finished, while search and graph indexing remain later checkpoints.

Each attempt keeps its own status, start/finish timestamps, and report. A single PDF cannot have two active claims within the 20-minute lease. A retry after lease expiry marks the interrupted attempt failed and replaces the current pointer. Finishing an obsolete or already-terminal attempt returns false, preventing stale writes and preserving a success after an ambiguous timeout. Row-level security restricts report reads to the owner. Authenticated clients receive no direct update privileges on documents or extraction rows. The ownership-checked RPCs can modify only the calling user's records.

This checkpoint runs one extraction at a time per API process. The UI polls status, supports closing the dialog and reloading, shows failures/retry, and loads page text/OCR additions and review notes on demand. First use may download public model artifacts. Background work is process-local rather than a durable external queue; server restarts, long processing, or expired user tokens can leave an attempt processing until a retry is eligible. The backend must stay running. The accepted timestamp in the POST response is the API acceptance time; subsequent status reads provide the database claim timestamp. No automatic retry or lease heartbeat is implemented. Production worker isolation, durable queues, and retention/deletion UI remain upcoming.

Tests cover API transitions, result persistence, safe failures, hidden documents, and timeout-after-commit recovery. `DIGITAL_MEMORY_RUN_DB_TESTS=1` enables rollback-only live database checks using a development user's ID and simulated database JWT subject claims. These verify RLS isolation, denied anonymous access, duplicate claims, lease recovery, and stale completion. They create no accounts or files and roll back synthetic metadata. Full live browser tests with two real authenticated users remain a separate validation step. The browser suite uses mocked accounts and verifies accepted processing, status refresh after closing, reload persistence, result display, and failure retry.

## Extraction checkpoint 5 - opt-in OCR fallback

Install the optional OCR lock from the workspace root:

```powershell
uv pip install --python .venv/Scripts/python.exe -r src/backend/requirements-ocr.txt
```

This lock adds ONNX Runtime to the existing locked layout environment. Regenerate it with `uv pip compile src/backend/requirements-ocr.in -c src/backend/requirements-layout.txt --python .venv/Scripts/python.exe -o src/backend/requirements-ocr.txt` when intentionally updating dependencies.

Add `--ocr` to the layout command. It can be combined with `--tables`:

```powershell
.\.venv\Scripts\python.exe -m src.backend.services.pdf_layout tests/fixtures/attention.pdf --document-id 00000000-0000-0000-0000-000000000001 --ocr --tables --output .cache/attention-ocr-v1.json --preview .cache/attention-ocr-v1.html
```

Choose fresh filenames. RapidOCR uses ONNX Runtime on CPU, English recognition, scale 3 (216 DPI), and Docling's `pdf_aware_layout_regions` mode. This mode processes candidate regions while retaining native PDF text. It supports image-only pages and image text beside native text. Engine filtering uses score 0.5; an additional score below 0.8 receives a review warning. These are initial heuristics, not calibrated accuracy thresholds. Other recognition languages and forced replacement of broken text layers remain future options.

Initial execution downloads public OCR model artifacts, after which document recognition runs locally. Remote inference services remain disabled. The package lock pins RapidOCR/ONNX Runtime versions, while model artifacts are selected by the installed Docling/RapidOCR registry and are not individually pinned by this adapter. Layout and table model revision records retain their earlier behavior. Model initialization requires installed optional dependencies and available artifact caches or download access.

`services/pdf_ocr.py` reads retained OCR line provenance, copies the engine's original text and recognition score, and saves normalized page boxes. Only lines explicitly marked `from_ocr` enter the OCR result. Native text remains under `native_extraction`. A separate `ocr_extraction` has its own run/region IDs but the same source hash and document ID. Existing `CitationReference` objects can resolve exact substrings against this OCR run. OCR lines remain `needs_review`, even with high model scores. Consumers must not treat native and OCR runs as interchangeable.

The JSON accounts for every physical page. Pages with native text and no OCR additions have an empty OCR region list. Missing OCR results are failures. Pages without native or recognized text are reviewable as possibly blank, graphical, or unreadable. Punctuation-only lines and suspect characters receive extra warnings while the original recognition is retained. Parsed page details are retained only during local conversion to preserve OCR provenance, increasing memory use. Reading order, handwriting, image equations, low-resolution scans, and detection completeness are not guaranteed. Native-glyph table superscript recovery cannot recover formatting from image-only cells.

The HTML preview shows recognized lines and scores beneath each original page, with warnings. These files contain source document content and belong in ignored local storage. This remains a local extraction checkpoint; queued processing, database persistence, and app integration follow later.

### OCR verification

Offline tests cover native/OCR separation, Unicode citations, missing results, low confidence, invalid boxes, suspect characters, and source consistency. A separate model test generates four synthetic pages (native, scanned, mixed, blank) and verifies recovery of budget/date text without duplicating the native note. Enable it explicitly, since it initializes local models and may download artifacts:

```powershell
$env:DIGITAL_MEMORY_RUN_OCR_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests/backend/test_pdf_ocr.py -q
Remove-Item Env:DIGITAL_MEMORY_RUN_OCR_TESTS
```

Default test runs skip that live model check. The synthetic fixture is generated in memory with no real personal information. These checks establish basic behavior and provenance, not a general OCR accuracy benchmark.

The verified synthetic sample recovered three lines from its image-only page and three from its mixed page, including `2000 USD` and `15 November 2026`. The native-only page received zero additions and the blank page remained reviewable. On the 15-page Attention fixture, combined OCR/table processing retained identical native text and all four tables. It returned 53 OCR lines on pages 3, 4, 13, and 14, mostly diagram labels. The last two pages of those results included punctuation-only artifacts. These require review, and model scores should not be treated as measured correctness.

## Extraction checkpoint 4 - structured tables

Add `--tables` to the local layout command to run TableFormer in accurate mode with native PDF cell matching. It uses the optional dependencies from checkpoint 3.

```powershell
.\.venv\Scripts\python.exe -m src.backend.services.pdf_layout tests/fixtures/attention.pdf --document-id 00000000-0000-0000-0000-000000000001 --tables --output .cache/attention-tables-v1.json --preview .cache/attention-tables-v1.html
```

Choose fresh output filenames for repeat runs. Initial use downloads TableFormer artifacts from `docling-project/docling-models` at the installed Docling implementation's `v2.3.0` revision. The observed local snapshot is `fc0f2d45e2218ea24bce5045f58a389aed16dc23`. Inference runs locally on CPU with remote services and OCR disabled. The report records table mode, model repository/revision, cell matching, and package versions.

`schemas/tables.py` stores a unique table ID, Docling source reference, physical page, normalized table box, dimensions, and cells. Each cell preserves its text, zero-based row/column ranges (end-exclusive), merged spans, and predicted column-header/row-header/section flags. It does not propagate values into blank cells or inherit settings from neighboring rows. Table text is a separate derived representation; existing native-text citation offsets retain their original meaning.

`services/pdf_tables.py` maps the structured output and renders HTML tables beneath their source pages. Cell spans must be within bounds and non-overlapping. Invalid tables and unsupported multi-page provenance generate report warnings. Uncovered grid positions display as blank cells with `data-extraction-state="uncovered"`, a tooltip, and an incompleteness warning. Actual predicted blank cells also stay blank. The distinction remains in the data, since a missing prediction cannot establish whether a source cell was blank. All tables remain `needs_review` until a later user-review workflow exists. Figures and equations continue to require specialized processing.

`services/pdf_table_formatting.py` uses native PDF glyph font sizes and baseline positions to add `text_runs` with baseline, superscript, and subscript markers. Cell boxes are normalized page coordinates. Runs must concatenate to the unchanged original cell text. The preview renders them with HTML `sup`/`sub` tags. Machine consumers should use these runs when interpreting powers; the raw `text` string alone remains flattened. Formatting is applied only when source glyphs align with cell text, with whitespace ignored. PDFium replacement glyphs may align with characters already extracted by Docling; this preserves Docling's text without independently verifying those glyph identities.

The script rule compares smaller glyphs (below 85% of the largest cell font) against nearby baseline glyphs. Raised/lowered baselines suggest super/subscripts. These are geometry heuristics, not formula recognition. Rotated pages, image-only text, mismatched glyph order, missing boxes, and unalignable text leave formatting unresolved and add review warnings. It does not reconstruct fractions, roots, or full equations.

The Attention fixture yielded four structured tables on pages 6, 8, 9, and 10. Their predicted dimensions were 5x4, 12x5, 21x13, and 13x3. Visual comparison of all four source table crops with rendered HTML confirmed the principal values and positions, including separate `58` and `32` cells on page 9. Pages 8 and 9 have uncovered grid positions, including intentionally empty source areas; the report conservatively flags those positions. In the corrected run, native glyphs aligned for all 222 predicted cells. The preview recovered three squared complexity terms on page 6, fourteen scientific-notation exponents on page 8, and the power-of-six parameter header and six subscript headers on page 9. Alignment counts are not text-accuracy scores. Row-group labels, merged boundaries, and model classifications still require review. This single-document inspection is not a measured table-accuracy benchmark.

Run ` .\.venv\Scripts\python.exe -m pytest tests/backend/test_pdf_tables.py -q` for offline checks of merged spans, blanks, escaping, out-of-bounds and overlapping cells, and unsupported provenance. The CLI and HTML preview remain local review tools; database storage and app UI integration follow later.

Run `.\.venv\Scripts\python.exe -m pytest tests/backend/test_table_formatting.py -q` for native-PDF and synthetic glyph checks covering raised powers, lowered subscripts, baseline digits, text preservation, and alignment failures. Corrected review outputs can be generated with fresh paths such as `.cache/attention-tables-v2.json` and `.cache/attention-tables-v2.html`.

## Extraction checkpoint 3 - local layout detection

Install optional dependencies from the workspace root:

```powershell
uv pip install --python .venv/Scripts/python.exe -r src/backend/requirements-layout.txt
```

The optional lock is constrained to the existing backend lock. To update it intentionally, run `uv pip compile src/backend/requirements-layout.in -c src/backend/requirements.txt --python .venv/Scripts/python.exe -o src/backend/requirements-layout.txt`.

Run layout analysis on an explicitly selected local PDF, choosing fresh output paths:

```powershell
.\.venv\Scripts\python.exe -m src.backend.services.pdf_layout tests/fixtures/attention.pdf --document-id 00000000-0000-0000-0000-000000000001 --output .cache/attention-layout-v1.json --preview .cache/attention-layout-v1.html
```

Open the HTML in a browser to inspect region boxes, labels, confidence scores, and per-page review warnings. It embeds rendered pages and requires no server. Both outputs contain source document content and belong in ignored local storage. Existing outputs are protected from overwrite. The example document ID is for local testing.

Docling 2.150.1 runs the Heron layout model on CPU. The model repository is `docling-project/docling-layout-heron`, pinned to revision `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`. Initial execution downloads public model weights, while PDF inference runs locally with remote services disabled. Subsequent runs can use the cached model with `HF_HUB_OFFLINE=1`. Optional dependencies and model weights have a substantial disk/memory footprint; this checkpoint has not been evaluated on free hosting.

`schemas/layout.py` defines separate layout annotations. `services/pdf_layout.py` maps Docling's one-based pages and coordinate origins into normalized top-left boxes. It retains the complete native extraction record and its citation offsets alongside the annotations. Detector labels and confidence are preserved, and unknown labels become reviewable regions. Detection order is not a guaranteed reading order.

Tables, figures, equations, unknown content, missing pages, invalid boxes, and model confidence below 0.5 trigger explicit page review statuses. The 0.5 threshold is an initial review heuristic, not a calibrated accuracy guarantee. A page marked `detected` means detections were returned without these warnings. Native extraction status and layout review status are distinct fields.

OCR, table-cell reconstruction, formula recognition, and figure interpretation are disabled at this checkpoint. Annotations identify regions requiring those later stages. Model boxes may be incomplete or overlap, and captions are not yet associated with figures. CPU/memory isolation, queued processing, persistence, and app integration remain future work.

### Attention fixture observation

The local 15-page run produced 167 text regions, 9 captions, 7 figure regions, 6 equation regions, and 4 tables. Tables were detected on pages 6, 8, 9, and 10. Equations were detected on pages 4 through 7. Figure regions occurred on pages 3, 4, 13, 14, and 15. Eleven pages require review. Region counts include subfigures and are not counts of numbered figures in the paper.

Visual spot-checks on pages 4, 9, and 13 confirmed useful boxes around the attention diagrams, displayed equation, model-variation table, and attention visualization. Boundaries are approximate; the page 13 figure box also includes its heading. This single-document review is not a precision/recall benchmark. Adapter tests run offline without downloading models; the HTML renderer smoke test runs when optional dependencies are installed.

## Extraction checkpoint 2 - native page text

`services/pdf_extraction.py` provides `extract_pdf_text(data, document_id=...)` and a local CLI. It returns the versioned extraction contract with a source hash, tool versions, page dimensions, physical page numbers, and one text region per page. Each run and region receives a fresh ID. Text is preserved exactly as returned by pypdf, including whitespace, for citation offsets.

From the workspace root, explicitly select a local PDF and a new JSON output path:

```powershell
.\.venv\Scripts\python.exe -m src.backend.services.pdf_extraction tests/fixtures/attention.pdf --document-id 00000000-0000-0000-0000-000000000001 --output .cache/attention-extraction.json
```

The example UUID is a local test identifier. Add the fixture first and ensure the output parent directory exists. Output files are created exclusively, so choose a fresh filename on subsequent runs. The JSON contains document text and should be kept in ignored local storage. The CLI prints a status summary and exits with code 1 for input or output errors. Inspect the run/page statuses for partial extraction failures, which are saved as reviewable results.

The extractor reuses the upload validator's 30 MiB and 1,000-page limits and rejects malformed, empty, and encrypted PDFs. Empty native output becomes `needs_review`; it does not automatically mean scanned content. Replacement characters and null characters also trigger review. A page extraction exception records a sanitized failure and processing continues for other pages. If all pages fail, the run is `failed`. Unreadable page geometry aborts the run because valid page references cannot be produced.

This checkpoint supports native text only. OCR, table structure, figures, reading-order verification, background jobs, database persistence, and API/UI integration remain upcoming. Successful native extraction does not establish complete page coverage or correct column order. Coordinates and model confidence are unset. Parsing is synchronous and has no hard CPU or decompressed-memory isolation, so this CLI is currently intended for explicitly selected local test files. `tests/backend/test_pdf_extraction.py` checks actual in-memory PDFs and command-line behavior.

## Extraction checkpoint 1 - data contract

`schemas/extraction.py` defines validated, versioned extraction records. This checkpoint defines data shapes for later extraction and persistence.

- `ExtractionRun` identifies the document, source SHA-256, extraction version, timezone-aware timestamp, tool versions, expected page count, and run status.
- `ExtractionPage` preserves the one-based physical PDF page number, optional printed label, dimensions in PDF points, regions, and warnings. Pending pages are permitted during processing. Completed runs must account for every page.
- `ExtractionRegion` records content type, extraction method, status, preserved text, optional coordinates, model confidence, and warnings. Confidence is a model output, not a validated accuracy score. Table cells and image artifacts will extend the contract when their extractors are introduced.
- Bounding boxes use normalized coordinates from 0 to 1, measured from the top-left of the displayed page. Parser adapters must convert their coordinates to this convention. Coordinates may be absent for the initial text-only parser.
- `CitationReference` identifies a document, extraction run, physical page, region, and end-exclusive Python character range. `resolve_citation` validates the reference and returns its exact text. Unicode offsets count Python string characters, not UTF-8 bytes or browser UTF-16 code units. Search normalization must preserve this original text separately.

Run ` .\.venv\Scripts\python.exe -m pytest tests/backend/test_extraction_contract.py -q` from the workspace root. Tests cover Unicode citation round trips, invalid references, coordinates, and incomplete or warning-bearing results. If `tests/fixtures/attention.pdf` is present, an additional read-only check builds a pending page inventory from its page dimensions and source hash. This fixture check does not establish text, table, or figure extraction accuracy.

We add one working layer at a time. Each checkpoint ends with a runnable result and checks before the next layer.

1. **API foundation (implemented):** FastAPI application, `GET /health`, API documentation, and request-level tests.
2. **Supabase document storage:** the migration is applied, catalog checks passed, and authenticated create/list/read API routes are implemented. Live two-user isolation testing remains pending.
3. **Text ingestion:** allow users to add their own documents; handle long texts in passages, keeping original text and source positions. Define size limits and clear errors, preserve Unicode, and prevent silent truncation. No bundled document corpus remains.
4. **Search and evidence:** search passages, paginate results, and return exact source references. Verify matches from the start, middle, and end of long files.
5. **Frontend integration:** connect the existing library and source previews, add loading/error/empty states, and configure local browser access.

Entity extraction, inferred event timelines, and relationship graphs follow reliable document storage and evidence retrieval. Document dates and dates mentioned inside documents must stay distinct.

## Checkpoint 2a: Supabase storage boundary

We are using Supabase from the start instead of SQLite. The planned request path is:

```text
Next.js -> FastAPI (user access token) -> document store -> Supabase PostgreSQL
```

Pydantic input and response schemas live in `schemas/documents.py`; the health response schema lives in `schemas/health.py`. These define Python validation and response shapes. The database table structure remains in `supabase/migrations/202611130001_documents.sql`; there are no ORM models because we use the Supabase Data API.

The `DocumentStore` interface in `storage/documents.py` defines create, list, and get operations. `SupabaseDocumentStore` implements them using Supabase's Data API through HTTPX. Routes can call these operations without knowing the database request details. Listing returns metadata only, so opening a library does not download every full book.

PostgreSQL stores the title, text, document date, collection, synthetic-data flag, owner, and creation timestamp. Original uploaded files will go into a private Supabase Storage bucket in the ingestion checkpoint; there is no bucket or file-upload integration yet. Supabase is our managed data service; Next.js and FastAPI will require their own deployment hosts.

### Connect the project in the next checkpoint

1. Create or select the intended Supabase development project.
2. Review `supabase/migrations/202611130001_documents.sql`, then use the migration command below. It creates the table, index, grants, and ownership policies in one tracked transaction. The initial migration has already been applied to the configured project; the runner safely skips recorded migrations.
3. Use the project URL and **publishable** key from the project settings. Add `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` to the workspace-root `.env`, preserving existing entries. Connection helpers load that file automatically; process environment variables take precedence. `.env.example` documents the names. Database passwords and secret keys are not needed for this Data API connection.
4. Enable the intended Supabase Auth sign-in method and create two development test users. The adapter requires a user's access token; a publishable key alone does not authorize document access.
5. Verify with user A: insert and retrieve a document. Verify with user B: A's document must not appear in a list or by ID. Requests without a user token must not read or create documents. Update/delete should be denied in this checkpoint.

Ownership comes from `auth.uid()`, not a client-provided owner ID. Row-level security lets each authenticated user read and insert only their own rows. The adapter uses a publishable key plus a user token and rejects new secret keys. Do not replace it with a secret/service-role key, which would bypass these policies.

The initial document migration has been applied to the configured Supabase project. Live database catalog checks verified all eight columns, the index, enabled RLS, both policy names, and the intended role permissions. Authenticated document routes now use the adapter. Offline tests do **not** prove two-user RLS isolation; that remains pending. Upload limits, passage splitting, and original-file storage are still upcoming.

### Apply migrations

After installing the updated requirements, run from the workspace root:

```powershell
.\.venv\Scripts\python.exe -m src.backend.supabase.migrate
```

This administrative command reads the project URL and `SUPABASE_DATABASE_PASSWORD` from the ignored root `.env` or process environment. It connects directly to PostgreSQL over encrypted TLS. Direct database access requires IPv6 connectivity or the project's IPv4 add-on; the Data API connection check alone does not establish database reachability.

The runner applies migration SQL and a checksum history entry in one transaction, with verification before commit. It uses `digital_memory_meta.schema_migrations`, a private application-owned history table separate from Supabase CLI migration history. A transaction lock prevents concurrent runs. Recorded files are skipped, changed checksums are rejected, and an untracked existing documents table requires review. Keep applied SQL files unchanged and add a new migration for future changes. Do not also apply these files manually or through the Supabase CLI without reconciling history first.

The migration creates the empty document table; it does not upload fixtures or create users. `/health` and the regular Data API adapter do not use the database password.

### Read-only connection check

After installing the updated requirements, run from the workspace root:

```powershell
.\.venv\Scripts\python.exe -m src.backend.check_supabase
```

This checks the Supabase Auth health endpoint with the configured publishable key. It prints no keys, passwords, tokens, or document content. A pass verifies service connectivity, not the document schema or user isolation. Missing configuration or failed requests exit with code 1.

After applying the migration and signing in a development user, run:

```powershell
.\.venv\Scripts\python.exe -m src.backend.check_supabase --documents
```

Paste that user's access token at the hidden terminal prompt; do not put it in a command argument or commit it. This verifies the user through Supabase Auth and reads at most one document's metadata. It makes no writes. Cross-user isolation still needs the separate two-user test described above.

`storage/connection.py` provides `open_document_store(access_token)` for standalone callers. HTTP document routes instead reuse the authentication dependency's request-scoped HTTP client and settings. `/health` remains independent of Supabase so the API can still start without cloud configuration. Connection helpers use direct HTTPS with a 15-second diagnostic timeout and do not use environment proxies.

References: [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys), [row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security), and [database and Storage architecture](https://supabase.com/docs/guides/getting-started/the-supabase-way-to-build).

## Backend authentication API

`GET /auth/me` is now implemented. It accepts `Authorization: Bearer <Supabase user access token>`, calls the configured project's `/auth/v1/user` endpoint, and returns only the verified user's `id`, `email`, `role`, and `email_confirmed_at`. No token decoding without verification is used. Supabase handles sign-in, passwords, session issuance, and refresh; this API does not implement a second password/login system.

Example response:

```json
{
  "id": "00000000-0000-0000-0000-000000000001",
  "email": "alex@example.test",
  "role": "authenticated",
  "email_confirmed_at": "2026-11-13T10:00:00Z"
}
```

To try it, start the backend, open http://127.0.0.1:8000/docs, select **Authorize**, and enter a signed-in Supabase user's **access token** (without the `Bearer` prefix). Then execute `GET /auth/me`. A project publishable key, refresh token, database password, or administrative key is not a user access token. Tokens expire; sign in or refresh through Supabase when needed. The endpoint does not impose an additional email-confirmation policy; that behavior follows the configured Supabase sign-in settings.

| Result | HTTP status |
| --- | --- |
| Verified authenticated user | 200 |
| Missing/malformed header or Supabase-rejected token | 401 with a Bearer challenge |
| Missing configuration, timeout, provider rate limit, or provider failure | 503 |
| Malformed user identity returned by the provider | 502 |

Responses use `Cache-Control: no-store`. Errors omit credentials and upstream response bodies. Each request is validated remotely, so protected endpoints depend on Supabase Auth availability. `/health` and API docs stay public. The reusable `get_authenticated_session` dependency retains the verified user and a masked access token used by document routes; tokens must not be logged.

Code is separated into `auth/dependencies.py` (token checks and validation), `auth/routes.py` (endpoint), and `schemas/auth.py` (response schema). Tests in `tests/backend/test_auth.py` mock Supabase for valid, expired/rejected, malformed, unavailable, and timeout scenarios. A successful mock test is not a live sign-in or a two-user RLS test. Frontend signup and sign-in are implemented; document UI integration remains upcoming.

## Document API

### PDF uploads

`POST /documents/upload` now accepts an authenticated `multipart/form-data` request with:

- `file` (required): PDF, up to **31,457,280 bytes (30 MiB)**, matching the current private `documents` bucket.
- `title` (optional): defaults to the PDF filename without the extension.
- `collection` (optional): defaults to `Unfiled`.

Open `/docs`, authorize with a Supabase user's access token, expand this endpoint, and choose a PDF in **Try it out**. Do not manually set the multipart Content-Type; the client must generate its boundary.

The endpoint checks the extension and supplied MIME type, reads the file with a byte limit, checks its PDF header, and opens its page tree with `pypdf`. Empty, damaged, encrypted/password-protected, and over-1,000-page files are rejected. Accepted MIME types are `application/pdf` and `application/octet-stream` for clients that do not identify PDFs correctly. Validating a PDF is not malware scanning or complete page rendering; parsing currently runs in a thread without hard CPU/memory isolation.

The original bytes are stored without overwrite under `documents/<user-id>/<document-id>/original.pdf`. Filenames are metadata, not storage paths. The same user's token authorizes both Storage and database requests; no secret/service-role key is used. A document row stores the original filename, byte count, page count, and private storage path. A successful response is 201 with the document and a `Location` header.

Uploaded PDFs have `status: "uploaded"` and empty `content`. This explicitly means **stored, not yet text-extracted**. Existing JSON text creates have `status: "ready"`. Scanned PDFs are accepted for storage, but OCR detection/extraction and page-level evidence have not been implemented. Uploading does not run a background worker or claim that the PDF is searchable.

Migration `202611130002_pdf_uploads.sql` has been applied to the configured project. It adds PDF metadata and permits authenticated users to insert/read only their own Storage folder. Cleanup may delete unlinked objects in that folder; linked originals are protected from this cleanup policy. No update/overwrite policy is added. This policy assumes no other permissive Storage policies grant broader access; the live bucket had no policies before this migration.

Storage and Postgres are separate services, so saving both is not a single atomic transaction. If metadata saving fails or times out, the endpoint attempts to remove the uploaded object. A policy refuses removal when a document row already committed, preserving the original after an ambiguous timeout. Cleanup failure emits a sanitized operational warning; administrative reconciliation may be needed. On a 503 after upload, refresh the document list before retrying, because a create is not idempotent.

Errors: 401 for missing/invalid authentication, 413 for too many file bytes, 415 for unsupported file type, 422 for invalid PDF or form fields, and 503 for storage/database failures. The application byte limit applies when reading the parsed uploaded file; also configure a multipart request-size limit slightly above 30 MiB at the deployment ingress, because multipart parsing/spooling happens before the route runs. The frontend now includes upload and document-list controls through Next.js proxies. The authenticated original-file endpoint `GET /documents/{document_id}/file` is now implemented for viewing/download.

Tests in `tests/backend/test_pdf_uploads.py` validate in-memory PDFs and mock cloud upload/metadata/cleanup requests. No real documents or accounts are created by the tests. Live user-token upload and two-user file access checks remain pending.

All operations require `Authorization: Bearer <user access token>`. Open `/docs`, authorize with a signed-in user's access token, and use the Documents section.

| Method and path | Result |
| --- | --- |
| `POST /documents` | Save text; returns full document with status 201 and a `Location` header. |
| `GET /documents?limit=20&offset=0` | Return a JSON array of owned document metadata, ordered newest first with ID as a tie breaker. |
| `GET /documents/{document_id}` | Return an owned document including its original text. |

Example create body:

```json
{
  "title": "My first note",
  "content": "Original text.\n\nAnother passage.",
  "document_date": "2026-11-13",
  "collection": "Personal",
  "synthetic": false
}
```

Only `title` and `content` are required for JSON text creation. Defaults are a null document date, `Unfiled` collection, and `synthetic: false`. Do not send `owner_id`, `id`, or timestamps: additional fields are rejected. Supabase derives ownership from the same verified access token forwarded by FastAPI. Text and paragraph breaks are preserved. The JSON endpoints do not import files; PDF upload is a separate multipart endpoint above. Passage splitting, search, update, and document deletion are not implemented. The JSON text-create endpoint has no content-size cap yet.

Lists omit full content and owner IDs, return `[]` when empty, and allow `limit` 1–100 and nonnegative `offset`. Invalid bodies, pagination, or UUIDs return 422. Missing/invalid authentication returns 401; a nonexistent or RLS-hidden document returns the same 404. Storage failures return a sanitized 503. Document responses, including validation errors, use `Cache-Control: no-store`. Avoid automatic retries of creates: duplicate submission currently creates separate documents.

`routes/documents.py` contains routes and storage wiring. Tests in `tests/backend/test_document_routes.py` cover the HTTP contracts, token forwarding, input validation, pagination, missing/hidden rows, error handling, and lack of token sharing between requests. Live end-to-end tests with two real accounts remain pending; no users or documents are automatically seeded by these tests.

## Health request flow

```text
Browser or test client -> GET /health -> FastAPI route -> JSON response
```

`main.py` creates the application. The route is a Python function that handles a particular HTTP method and path. `HealthResponse` defines the response shape. Uvicorn runs the app and accepts HTTP requests; FastAPI routes those requests to the function.

The response is `{"status":"ok","service":"digital-memory-api"}`. It proves that the application responds, not that a database or ingestion pipeline is ready. This checkpoint performs no document imports and makes no changes to the frontend.

## Install and run

From the workspace root in PowerShell, using the existing Python environment and installed `uv` package manager:

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r src/backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --app-dir src --reload --host 127.0.0.1 --port 8000
```

The development server runs until Ctrl+C. Open:

- http://127.0.0.1:8000/health — check the JSON response.
- http://127.0.0.1:8000/docs — expand `GET /health`, select **Try it out**, then **Execute**.

Or run `Invoke-RestMethod http://127.0.0.1:8000/health` in another PowerShell terminal.

No credentials or configuration files are required. The server binds to the local machine. The application has no authentication yet.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`pytest.ini` adds `src` to the Python import path and selects `tests/backend`. Tests send requests in process; they do not need a running server. They cover health JSON, the schema and docs, unknown routes, and unsupported HTTP methods. No formatter or linter is configured yet.

`requirements.in` declares direct runtime and development dependencies. `requirements.txt` locks the resolved dependencies. To intentionally update the lockfile:

```powershell
uv pip compile src/backend/requirements.in --python .\.venv\Scripts\python.exe -o src/backend/requirements.txt
```

## Original PDF viewing endpoint

`GET /documents/{document_id}/file` requires the same bearer authentication as other document routes. It reads the owned document row before accessing private Storage and requires the canonical `<user-id>/<document-id>/original.pdf` path. Missing, hidden, text-only, or mismatched paths return 404 without fetching the object. Storage authentication expiry returns 401, inaccessible/missing objects return 404, and service failures return 503. Oversized or non-PDF stored bytes return 502.

The successful response returns original bytes as `application/pdf`, with inline Content-Disposition, UTF-8 filename encoding, no-store, and nosniff headers. No signed or public URL is issued. The backend buffers at most 30 MiB before responding; it does not implement range requests. The frontend fetches a full file through its server proxy before displaying a temporary browser-local URL. No migration or text extraction is needed for viewing. Tests mock Storage and verify byte preservation, ownership/path checks, content headers, authentication, and failure handling.

## Phase 1 - plain-text notes

The workspace has an **Add a note** form (title, optional collection and date, up to 200,000 characters). `POST /documents` already stored text documents; it now enforces that 200,000-character limit and rejects text that is not valid Unicode. A saved note then goes through `POST /documents/{id}/process-text`, which makes it usable by the same extraction, passage and Entities pipeline as a PDF. Opening a note shows its exact text and the unchanged Entities review panel. A note that was saved but not prepared (for example after a network failure) shows a **Prepare note** button.

**Adapter (`services/text_notes.py`).** A note becomes a one-page, one-region extraction whose region text is the note, character for character, with CRLF, Unicode and trailing whitespace untouched. A citation's `start_char` and `end_char` are therefore offsets into the note itself. I did not split the note into paragraph regions: `source_passages` already cuts a region into passages of at most 1,200 characters, preferring paragraph, then sentence, then word boundaries, and one region keeps every offset global with no region-to-note mapping to maintain. Page geometry (612 by 792) is a placeholder, and notes carry no table, OCR or layout data. Run and region IDs derive from the document and the SHA-256 of the exact text.

**Database (`202611180001_text_notes.sql`).** One function, `create_text_extraction(document, run, report)`, is security definer with an empty search path, owner-checked, executable only by signed-in users, and refuses PDFs. It recomputes the SHA-256 of `documents.content` and requires the report to carry the same hash, document ID and text, with one page, one region, and no OCR or tables, so a client cannot attach a report that does not match the stored note. It inserts a `ready` extraction and points the document at it.
- **Same text again:** the existing extraction is returned and nothing changes, so entity runs and review decisions stay attached.
- **Changed text:** clients cannot update content (no UPDATE grant), so this only happens through an administrative edit. A new extraction replaces the current one. Its region IDs derive from the new hash, so review decisions made on the old text cannot carry over (carry-over matches on region ID). Older entity runs remain as history, and the entity list shows the most recent run, so after such an edit it keeps showing the old run until a new one is started.

Verified against the live database inside a transaction that was rolled back, including CRLF and Unicode text, idempotence, refusal of tampered reports, PDFs and other users, and no decision carry-over after an edit. Apply it with `python -m src.backend.supabase.migrate`.

**Limits.** Notes are immutable through the API, so there is no edit flow yet. The 200,000-character cap means at most about 170 passages per note, so a maximum-size note will likely take a few minutes of model time (an estimate from the 93-passage paper run, not measured on a note). A real-model run on a short pasted note returned 14 suggestions, all with exact citations, with small boundary quirks (`Saturday` rather than `Saturday evening`, `Café Aylanto` as an organization rather than a location).

## Entity identity (Phase 2)

Accepted mentions become per-user **canonical entities** that span documents. Apply `202611180002_entity_identity.sql` (`python -m src.backend.supabase.migrate`) first. It was validated, not applied, by the author: `tests/backend/check_entity_identity_live.py` runs the migration and 38 checks against the real database inside a transaction that is rolled back (grouping, merge, undo, split, link, rename, dismiss, a re-run, a second user, and anon).

**Model.** `entities` (type, canonical name, status `suggested`/`confirmed`/`merged`), `entity_links` (one accepted mention to one entity, with method), `entity_merges` (history with the moved links, so a merge can be undone) and `merge_suggestions` (pair, kind, score, ambiguous flag, reason, status). All four have row-level security with select-own and no direct writes. Writes go through owner-checked `security definer` functions: `refresh_entity_links`, `apply_entity_resolution`, `merge_entities`, `undo_merge`, `link_mention`, `split_mention`, `rename_entity`, `confirm_entity`, `dismiss_merge_suggestion`. Only **accepted** mentions of a document's **latest** completed run are linked; their effective type is `coalesce(reviewed_label, label)`. A mention never joins an entity of another type, and entities of different types never merge. When a document is re-run, `refresh_entity_links` moves existing links (including manual ones) onto the equivalent mention of the new run, drops links whose mention was rejected, retyped or no longer present, and deletes unconfirmed entities left empty.

**Policy.** Nothing merges without a person. `POST /entities/resolve` only (1) links an accepted mention to an existing entity when its normalized text equals one of that entity's spellings (if several entities qualify it is left unlinked and counted as `skipped_ambiguous`), (2) groups the remaining identical-normalized same-type mentions into a new entity with status `suggested`, and (3) refreshes merge suggestions. Everything fuzzy is a suggestion carrying a reason and example mentions with their citations. Decided suggestions stay decided across later resolves.

| Type | Auto-grouped when | Suggested for merging |
| --- | --- | --- |
| person | Same name after ignoring case, accents, titles (Dr., Prof.), suffixes (Jr.), `et al.` and word order (`Page, Larry` = `Larry Page`) | Initial or missing middle name: `L. Page`, `L Page`, `Larry E. Page` vs `Larry Page` (score 0.8 to 0.85). Surname only: `Page` vs `Larry Page` (weak, 0.4, never linked automatically) |
| organization | Same name after ignoring legal suffixes (Inc., Corp., Ltd, LLC, GmbH) | One name extending the other (`Stanford` / `Stanford University`, but also `Google` / `Google Brain`): weak, 0.45, with a note that it may be a part of the organization |
| location, date, event, project, model, method, dataset, component | Same name after ignoring case, accents and punctuation (`Self-Attention` = `self attention`) | Nothing |

Two full first names that differ (`Larry` / `Lisa`, `Michael` / `Mike`) are a conflict and never suggested. If one name fits two entities that conflict with each other, as `L. Page` does for `Larry Page` and `Lisa Page`, each suggestion is flagged **ambiguous** and its reason names the rivals. Ambiguity flags are computed within one type.

| Route | Purpose |
| --- | --- |
| `POST /entities/resolve` | Refresh links, group new entities, refresh suggestions |
| `GET /entities?type=&q=&status=&limit=&offset=` | Entities with mention and document counts and spellings (merged ones hidden) |
| `GET /entities/{id}` | One entity with every supporting mention, citation and merge history |
| `GET /entities/suggestions?status=pending&min_score=` | Suggestions with reason and up to three example mentions per side |
| `POST /entities/suggestions/{id}/confirm` (optional `{"keep_id"}`) / `.../dismiss` | Merge (survivor defaults to the entity with more mentions) or dismiss |
| `POST /entities/merge`, `POST /entities/merges/{id}/undo` | Manual merge of two same-type entities, and undo |
| `POST /entities/mentions/split`, `POST /entities/{id}/link` | Move a mention into a new entity (this is how a mention is unlinked), or into another entity of the same type |
| `PATCH /entities/{id}`, `POST /entities/{id}/confirm` | Rename (also confirms) or confirm |

The workspace has a new **Entities** view: update entities, review possible merges with reasons and source text, merge into either side or mark "not the same", open an entity to rename, split off a mention, merge another entity into it, and undo a merge.

**Measured, with limits.** `tests/fixtures/name_variant_pairs.json` has 56 authored pairs (written before measuring; run `python tests/backend/evaluate_name_matching.py`). Persons: 22 of 24 same-person pairs are suggested for merging (91.7% recall), and none of the 13 different-person pairs is (no false merges); the 2 misses are nickname pairs (`Michael`/`Mike`, `Robert`/`Bob`), which this matcher does not handle. All 4 surname-only or initial-only "possible" pairs got suggestions, as designed. Organizations: 4 of 5 same pairs suggested strongly (`Stanford`/`Stanford University` is weak), no strong false merges, but `Google`/`Google Brain` and `Apple Inc.`/`Apple Records` get weak suggestions that a reviewer should reject. One pair (`Mary-Jane Watson`/`Mary Watson`) was merged in the first run and fixed by keeping hyphenated given names whole; it was changed after seeing that result, so it is not held out. The set is small, written by the author of the matcher, and covers Western-style names with a few transliterations. It says nothing about names with no stable surname position, single-name people, or non-Latin scripts.

**Known limits.** Aliases are exact spellings of linked mentions, so a split entity that shares a spelling with another entity leaves later identical mentions unlinked until someone links them. Undoing a merge restores the links recorded at merge time; links created since stay with the surviving entity. Resolution compares every entity pair of a type, which is fine for thousands of entities but not for hundreds of thousands. `POST /entities/resolve` is synchronous. Suggestions are only as good as the accepted mentions: a wrongly accepted mention becomes a wrong entity until a person splits it.
