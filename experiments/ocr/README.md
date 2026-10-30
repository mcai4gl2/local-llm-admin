# OCR qualification

The [references and thresholds](references.json) are frozen before candidate inference.
All reference text is repository-authored and public-safe. No customer documents are used.
The generator produces raster PNGs and a three-page scanned PDF. Generated outputs contain
no font files. The available WenQuanYi Zen Hei font is GPL-2 with a font embedding exception;
its Debian copyright notice permits documents without imposing the font license on them.
Keep its notice with a separately distributed font; this repository does not redistribute it.

Generate inside the devcontainer:

```sh
uv run --with Pillow==12.3.0 python experiments/ocr/generate.py \
  --font /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
  --font-sha256 79c18ebe7b811951e8311bad7103ebeae8c337ed9988ea69e8a78a66cfe029b9 \
  --output .private/ocr-corpus
```

Preserve the generated file hash manifest before inference. With another licensed font,
record its hash/license and freeze a new corpus version rather than silently replacing it.
PDF metadata can change its binary hash between generations; the saved original corpus is
the qualification input. The frozen corpus was generated locally. [Native outcomes](../../docs/reports/ocr/20261031-native-rollout.md)
include the original failures and the qualified bounded operating point.

In an approved live window, record structured native results keyed by reference case name
in a private `results.json`; send every case through the full pipeline. Confirm image and
PDF preservation separately through actual LiteLLM `local-ocr` requests using
`response_format: json_object`. Do not use the raw VLM endpoint as document qualification.

Capture a baseline of at least 30 chat and 30 embedding requests, then the same workload
with OCR active/queued using these public inputs. Hold model IDs, context, request prompts,
sampling and concurrency fixed. Save p95 latency, chat completion tokens/second and errors
as `chat_p95_ms`, `chat_tokens_s`, `embed_p95_ms`, `errors`. Unknown usage stays unknown.
Measure idle/peak RSS and system pressure throughout with private Mac sampling; reservation
must include all OCR components and rendering peaks, and total reservations plus headroom
must fit RAM/wired limits. No load pass can waive unknown or elevated pressure.

```sh
uv run python -m local_llm_admin.ocr_qualification \
  --references experiments/ocr/references.json --results .private/ocr/results.json \
  --baseline .private/ocr/baseline.json --combined .private/ocr/combined.json \
  --output .private/ocr/scores.json
```

The evaluator refuses to overwrite reports and returns failure for missing task/load
evidence. English/numeric/columns require CER <=2%; CJK/rotation/table/formula <=5%; blank
requires no text. Page order/completeness, numeric fields and table rows/cells must be exact.
Load thresholds: chat and embedding p95 <=1.25x baseline, chat throughput >=0.90x, zero errors.
Collect enough requests and memory evidence separately before claiming qualification;
the scoring command checks supplied summaries, not their measurement provenance.

Failed or inconclusive runs remain frozen. Publish only sanitized scores, aggregate memory,
timings, pins and limitations under [reports](../../docs/reports/ocr/INDEX.md). Raw route
definitions, deployment identities, request metadata and credentials remain outside git.

The reusable collector previews by default. Supply a private config such as
`{"base":"http://litellm.example.com:4000","chat_model":"local-model","embed_model":"qwen3-embedding","api_key_env":"LITELLM_CLIENT_KEY"}`.
Use actual allowed public route names privately; the key is read from the environment.
In the approved window, append `--run` to each command:

```sh
uv run python -m local_llm_admin.ocr_benchmark --config .private/ocr/config.json \
  --phase corpus --corpus .private/ocr-corpus --output .private/ocr/results.json
uv run python -m local_llm_admin.ocr_benchmark --config .private/ocr/config.json \
  --phase baseline --corpus .private/ocr-corpus --output .private/ocr/baseline.json
uv run python -m local_llm_admin.ocr_benchmark --config .private/ocr/config.json \
  --phase combined --corpus .private/ocr-corpus --output .private/ocr/combined.json
```

The corpus phase verifies frozen input hashes and collects structured results through
LiteLLM. Load phases issue 30 chat and 30 embedding calls; the combined phase runs a single
OCR competitor until they finish. There are no HTTP retries, and an OCR failure stops the
competitor. Outstanding work completes before the collector releases it. Reports are
private and exclusive-create; missing token usage remains null. Scoring requires at least
30 successful chat and embedding samples per phase as well as zero errors.

Supply `--memory .private/ocr/memory.json` to the scorer after native measurements. Its
required fields are `pressure_normal: true` and positive GiB values `ocr_peak_gb`,
`reservation_gb`, `aggregate_peak_gb`, `ram_gb`, `wired_limit_gb`, `headroom_gb`. Measure the
complete OCR working set and system impact, not just parameter count or one process RSS.
Keep sampling provenance privately. Unknown memory, elevated pressure, an undersized
reservation or exceeding RAM/wired capacity fails qualification. The scorer always marks
missing memory evidence as pending, even when accuracy and latency pass.
