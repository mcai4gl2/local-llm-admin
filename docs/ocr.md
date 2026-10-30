# Resident OCR

PaddleOCR-VL is an opt-in resident service beside embeddings. The approved native release
passed the fixed public corpus, joint load, memory and lifecycle checks for one configured
chat model. See the [native report](reports/ocr/20261031-native-rollout.md) for the measured
scope and preserved failures, and the [host guide](../host/ocr/README.md) for operations.
Additional sharing models and long-running allocator behavior remain unqualified.

The Models page renders every registered resident service. Start and Stop affect that
service independently. Exclusive engines suspend residents and keep their running intent;
switching back restores compatible residents in order, with a fresh aggregate memory check.
An explicit Stop clears that intent, including while suspended. There is no boot autostart
or automatic crash restart. Failed readiness raises an alert and keeps discovery unavailable.

The OCR page accepts a PNG, JPEG or PDF and offers text, Markdown and JSON downloads.
Output is escaped. A cancellation or deadline terminates the owned document worker and
VLM when safe cancellation cannot be proven; the service then needs an explicit restart.
Unknown cleanup keeps admission busy. Routine logs and SQLite contain numeric metadata,
not document bytes, filenames or extracted text. Temporary files are mode 600 in private
request directories and are removed on completion, failure, cancellation and startup.

## Contracts

OCR uses the existing sidecar gateway, independently of the selected chat-engine gateway.
Both OCR endpoints always require a valid existing gateway client key, even if anonymous
access is enabled for older routes. The admin upload uses its existing session and CSRF
protection. Internal API and VLM ports bind loopback only.

Native multipart:

```sh
curl --fail-with-body "$SIDECAR_BASE/v1/ocr" \
  -H "Authorization: Bearer $SIDECAR_GATEWAY_API_KEY" \
  -F 'document=@public-reference.pdf;type=application/pdf' -F output=json
```

`output` is `json`, `text` or `markdown`. The single file field is `document`; other
fields and multiple documents are rejected. Source filenames are ignored.

LiteLLM and direct gateway chat use public model `local-ocr` and one user turn. For an
image, send `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`.
For a PDF, send `{"type":"file","file":{"file_data":"data:application/pdf;base64,..."}}`.
JPEG uses `image/jpeg`. These are inline payloads; remote URLs, file IDs and local paths
are unsupported. Streaming, tools, arbitrary chat instructions and generation parameters
are rejected. The default assistant content is Markdown. Add
`"response_format":{"type":"json_object"}` for a serialized structured result.
The exact envelopes are exercised by [contract fixtures](../tests/ocr_fixtures.py).
Optional fixed instructions accepted by the adapter are documented in its validator;
clients should omit text parts when they only need recognition.

```python
import base64
import os
from pathlib import Path
import httpx

data = base64.b64encode(Path("public-reference.pdf").read_bytes()).decode()
response = httpx.post(
    os.environ["LITELLM_BASE_URL"] + "/v1/chat/completions",
    headers={"Authorization": "Bearer " + os.environ["LITELLM_CLIENT_KEY"]},
    json={"model": "local-ocr", "stream": False,
          "messages": [{"role": "user", "content": [
              {"type": "file", "file": {"file_data": "data:application/pdf;base64," + data}}
          ]}], "response_format": {"type": "json_object"}},
    timeout=300,
)
response.raise_for_status()
print(response.json()["choices"][0]["message"]["content"])
```

Do not automatically retry an ambiguous timeout. A caller disconnect may require stopping
the OCR generation before another request can enter. Suspended/stopped services return 503
and are never started by inference. Full queues return 429 with `Retry-After: 1`; invalid
envelopes return 400/415, over-limit documents 413, corrupt/encrypted PDFs 422, invalid or
truncated upstream results 502 and document deadlines 504. Errors omit native exception text.

Schema v1 includes model/revision/precision, `coordinate_units: pixels`, ordered pages with
width/height, text, Markdown, typed blocks and optional `[x0,y0,x1,y1]` geometry. It explicitly
marks empty pages and requires complete documents with `truncated: false`. No confidence
score or token usage is fabricated. Chat completion responses omit unknown `usage`.

## Candidate limits and settings

| Bound | Initial ceiling |
|---|---|
| Decoded input | 10 MiB |
| Image/rendered page | 16 million pixels |
| PDF | 16 pages, rendered incrementally at 144 DPI |
| Deadline | 45 seconds/page, 240 seconds/document |
| Output | 2 MiB validated JSON |
| Admission | One active document, two waiting, 30-second queue wait |
| Region inference | One VLM request at a time |

These bounds are enforced contracts, not measured memory or latency guarantees. The
service reservation includes layout, VLM, API and peak rendering work. `OCR_FOOTPRINT_GB`
defaults to zero so admission refuses until an operator sets an explicit qualification
reservation. Record measured peak before claiming a production sharing budget.

Set `OCR_ENABLED`, `OCR_ROOT`, explicit `OCR_PORT`/`OCR_VLM_PORT`, `OCR_ALLOWED_MLX`,
`OCR_FOOTPRINT_GB` and `OCR_READY_TIMEOUT_S` privately. Align the private resident catalog
with the same admin settings. API/model/port metadata drift refuses new admission.
The public ID defaults to `local-ocr`; revision and BF16 precision must match the host.
Existing `EMBED_*`, `embed_intent`, public embedding/reranker IDs and old request tables
retain their contracts. Generic service history adapts old embedding rows without copying.

## LiteLLM route operations

The [template](../ops/examples/litellm-ocr.json) targets the sidecar gateway, never LiteLLM
itself or the selected-engine inference gateway. Its timeout is 300 seconds and both retry
settings are zero. Supply the gateway key through the LiteLLM server's environment.
If that variable is absent on the proxy, `--resolve-key-env` reads the existing gateway key
from the local environment and sends it only at the management request boundary. Preview
output retains the environment reference. This avoids restarting the proxy to add a variable;
LiteLLM stores the key with the requested route. Existing-route rollback then requires the
previous credential explicitly in the private definition, never the candidate key by inference.
A view-only admin key can preview; mutation requires an appropriate proxy-admin key.

Inside the devcontainer, with secrets supplied privately:

```sh
uv run python -m local_llm_admin.ocr_route --gateway-base http://mac-mini.local:8082/v1
# Explicitly approved rollout only:
uv run python -m local_llm_admin.ocr_route --gateway-base http://mac-mini.local:8082/v1 \
  --apply --snapshot .private/ocr-route-rollback.json
uv run python -m local_llm_admin.ocr_route --rollback --snapshot .private/ocr-route-rollback.json
```

Only `local-ocr` changes. The tool rechecks route identity before mutation and saves a
mode-600 receipt first. Existing routes require a complete private
`--previous-definition` because inventories may hide credentials. Multiple matching
deployments, feedback loops, config-file routes, drift and opaque rollback definitions
are refused. An ambiguous management response leaves receipt state `unknown`; inspect
the live inventory privately before any manual recovery. There is no automatic retry.
Configured routes can remain registered while OCR is suspended.

The installed LiteLLM 1.99.1 SDK preserved both inline images and PDFs in isolated gateway
tests. That is SDK forwarding evidence with a stubbed pipeline, not a live proxy or native
OCR pass. Reproduce with the same installed SDK in an isolated environment, never by
upgrading the running proxy. [The integration test](../tests/integration/test_litellm_ocr.py)
requires exact version 1.99.1; ordinary tests skip it if that optional SDK is absent.

## Visibility and qualification

Models cards expose per-service intent, ownership suspension, readiness, budget, traffic
and log links. OCR routine logs are `ocr` and `ocr-vlm`; embedding logs are `embed`.
Diagnostics check each descriptor and report dependency/readiness loss. Logs rotate at
50 MiB with eight archives. Service availability and request metadata use the existing
observability retention setting. A missing telemetry value is unknown, never zero traffic.

Generate and freeze the [public corpus](../experiments/ocr/README.md) before any candidate
inference. Its evaluator checks character error rate, exact numeric fields, page order,
completeness and separate table cells/rows. Freeze a baseline with the same chat/embedding
models, context, workload and at least 30 successful requests in each phase; qualify the
combined OCR workload against it. Preserve raw private measurements and publish sanitized
pass/fail outcomes without replacing failed evidence. Rotation and formula failures remain
qualification failures even if the transport works.

Native region inference is serial and paced: each completed region reserves an idle
interval equal to its call duration, capped at one second, across document boundaries.
Page/document deadlines include that wait. This reduces OCR throughput to protect
interactive chat; cancellation is rechecked before any delayed region is forwarded.
