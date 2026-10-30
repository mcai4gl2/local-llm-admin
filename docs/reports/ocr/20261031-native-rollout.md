# Native PaddleOCR rollout — 2026-10-31

Outcome: released and running as `local-ocr`. The fixed public corpus, joint load,
measured memory envelope and native lifecycle checks passed for one configured chat
model alongside embeddings. OCR sharing is restricted to that measured model. This
is a bounded operating-point qualification, not a claim about other models, arbitrary
documents or long-running allocator growth. Defaults remain opt-in.

The [frozen qualification artifact](20261031-qualified.json) contains exact scores and
sanitized measurement inputs. Private env/catalog, credentials, raw diagnostics,
backup databases, deployment manifests and route/provision receipts remain outside git.
Verified 27 native bundle files and 105 admin source files against the working tree. A
private qualification receipt records the exact release and evidence hashes. No commit
was made. The [host runbook](../../../host/ocr/README.md) describes operations
and rollback; the [API guide](../../ocr.md) defines client contracts.

## Native pipeline and corrections

CPython 3.12, PaddleOCR 3.6.0 / PaddlePaddle 3.2.1, MLX-VLM 0.7.3 / MLX 0.32.2,
BF16. VLM, layout and document-orientation revisions are frozen in the JSON artifact.
The original VLM/layout pins were retained; the CPU orientation model was added explicitly.

The classifier sees a padded content view trimmed only at exact-white margins. Layout
and recognition receive the complete page after rotation. Boxes map back to original
input pixels. Clearly separated text-column gutters are read down each column;
ambiguous or anchored layouts retain upstream ordering. Plain text removes whole-block
math presentation delimiters; Markdown retains its formatting.

Native startup exposed a missing versioned OpenAI base and formatted block text in the
warmup comparison. Native stop tracing exposed an observer inheriting the service process
group. Inventory observers now run in separate sessions. An ambiguous exiting generation
is never signaled; cleanup waits within a bound to prove the group gone, then removes
only the unchanged record. Explicit generation-checked Stop also verifies stale-record
cleanup when processes are already down. These regressions failed before their fixes.

## Accuracy and load

All 10 unchanged corpus tasks passed with zero CER: English, simplified/traditional
Chinese, exact numeric fields, rotation, columns, table cells/rows, math, blank input and
a three-page scanned PDF. Inputs and thresholds were never relaxed after observing results.
Actual image and PDF requests traversed the deployed LiteLLM proxy and native full pipeline.

| Metric | Baseline | OCR combined | Ratio / requirement |
|---|---:|---:|---|
| Chat p95 | 952.64 ms | 1050.54 ms | 1.103x; <=1.25x |
| Chat completion throughput | 73.38 tokens/s | 67.12 tokens/s | 0.915x; >=0.90x |
| Embedding p95 | 1639.23 ms | 1608.76 ms | 0.981x; <=1.25x |

Each phase completed 30 chat and 30 embedding requests with zero errors. Combined load
completed 32 serial OCR documents. OCR regions idle for the previous call duration,
capped at one second, across document boundaries. Existing deadlines include pacing;
admission is checked again after the wait. This explicitly trades OCR throughput for
interactive capacity. The unpaced candidate failed and remains recorded below.

## Memory and lifecycle

459 samples at a nominal two-second interval covered 15.69 minutes, including
load, maximum-size rendering, forced stop and engine transitions. All pressure samples
were normal. Per-process lifetime peak physical footprint includes GPU-backed memory,
all OCR components and observed descendants; summing peaks conservatively bounds usage.

- OCR peak bound: **5.32 GiB**, within its retained **12 GiB reservation**.
- Aggregate managed-model/service peak bound: **37.42 GiB**; plus **6 GiB** headroom fits **64 GiB** RAM.
- The aggregate bound also fits the device-reported **51.84 GiB** Metal recommended working set, used conservatively because the kernel override returned zero. No kernel tuning was applied.
- System wired-memory peak: **28.87 GiB**. Raw VM counters and sampling provenance remain private.

The 16-megapixel image and 16-page PDF completed; PDF page markers and order were exact.
Oversized image and 17-page PDF returned 413; corrupt PDF returned 422. Anonymous OCR
returned 401 and remote-URL input returned 400. Paused OCR returned 503 without starting
models. During active work, ordinary stop and exclusive switch returned 409; forced stop
interrupted the request and verified every OCR component down.

A ComfyUI round trip paused both residents while retaining running intent, then restored
embeddings and OCR alongside the original chat model and identical profile. Final actual
proxy calls for chat, embeddings, reranking, image OCR and PDF OCR all returned 200.
Initial failed warmups also exercised dependency cleanup. First-stop failures were
preserved and corrected before the successful native transition checks.

## Preservation, validation and limits

The additive schema-17 DB passed integrity checking. All original rows in 19 historical
tables matched the consistent pre-release snapshot, and existing clients were unchanged.
All pre-existing proxy route definitions remained identical; only `local-ocr` was added.
Private pre-release DB/config/app backups and reversible route/provision receipts remain
available. An older admin still requires its matching pre-migration database on rollback.

Final serial gate: **3,844 passed, 4 skipped; 97.24% coverage**. Ruff and strict mypy passed.
Real CPU image/PDF and combined crop/rotation tests passed separately with candidate pins.
The prior 116-browser-test result covers the unchanged UI. An obsolete full run interrupted
when native changes superseded it remains private; it is not counted as a final pass.

A pre-existing long-running embedding process held about 49 GiB physical footprint before
qualification. Restarting it reduced that to about 5.85 GiB. This rollout qualifies the
measured fresh workload, not indefinite cache growth. Broader sharing and allocator drift
are tracked in the [resident follow-up](../../../plans/backlog/platform/20261031_resident-sharing-followup.md).

## Preserved unsuccessful outcomes

- [Initial corpus](20261031-initial-corpus.json): 7/10 tasks; columns, rotation and math failed.
- [Second corpus](20261031-second-corpus.json): 9/10; rotation still failed.
- [Third corpus](20261031-third-corpus.json): rotation passed, but cropping layout changed columns.
- [Unpaced load](20261031-unpaced-load.json): accuracy passed; chat p95/throughput failed.
- [Local implementation report](20261030-local-validation.md): earlier local failures,
  platform resolution and partial investigations remain historical.

Primary integration references: [PaddleOCR-VL usage](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html),
[Apple Silicon deployment](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html),
[orientation model](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_doc_ori/tree/d3b95a6dff5fe8a94f2748e12b61cb26818a0df8).
