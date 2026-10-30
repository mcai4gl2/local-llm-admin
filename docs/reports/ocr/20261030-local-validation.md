# OCR local implementation evidence — 2026-10-30

Status: local validation passed; native runtime and live LiteLLM route unqualified.
No model inference, package installation, model download, service change or LiteLLM route
write was performed on the Mac during this implementation session.

The candidate is PaddleOCR-VL-1.6 BF16, revision
`c5630abae1d940eafe0697512a0325494b02ab42`, with PP-DocLayoutV3 revision
`241f8bdfc77a7c7bee915a5057aaee58c235a8d3`. The
[Apple Silicon guide](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html)
describes the full pipeline with MLX-VLM region recognition. This is interface evidence,
not proof that this exact dependency set works on the deployment.

Both isolated hashed environment locks resolved for CPython 3.12/macOS 14 arm64.
The initial default macOS 13 target failed MLX wheel compatibility; it was not an install
success. Native package versions are pinned in the [host guide](../../../host/ocr/README.md).

Read-only host SSH verified Apple Silicon and an available pair of candidate loopback ports.
The container could not authenticate to the Mac; host SSH succeeded after user direction.
Actual topology, compatibility names, configuration and credentials remain private.

Read-only inspection identified the installed LiteLLM SDK as 1.99.1 (image source revision
`10f4033437df30b91b5dbf2b64711d0a8683fc52`). That version was unavailable from the configured
Python package index. An isolated read-only source copy of the installed SDK passed two
tests forwarding exact image and PDF bytes through the real gateway to a stubbed pipeline.
An initial test teardown failed across separate event loops; a shared test loop and bounded
logging cleanup passed. No provider hook was required in that SDK check. Actual proxy HTTP
forwarding, live route registration and native recognition remain pending.

Initial whole-suite validation before the host/runtime modules: 3,587 passed, two skipped,
two pressure-fixture failures; coverage 91.9%. Both failures later passed focused reruns.
That run is preserved as partial evidence, not final validation. Browser tests after generic
card/log links and OCR UI changes: 116 passed on isolated Chromium.

The public reference corpus and acceptance thresholds were fixed before candidate inference.
No accuracy, memory, warmup, OCR latency or combined-load results exist yet. The candidate
disables orientation preprocessing, so rotated references require particular qualification.
Default OCR reservation is unknown and refuses admission until explicitly configured.

Final local evidence:

- Full suite: 3,794 passed, three skipped; 96.52% coverage. Ruff and strict mypy passed.
- After the final concurrent-start cleanup correction, 264 lifecycle/policy tests passed.
  OCR jobs use a unique start token for conditional cleanup; a replacement transaction is
  preserved. A further start/restore test passed with that protocol through a fake host CLI.
- Critical OCR transport/history: 119 passed, 100% statement and 99% branch coverage.
- Real CPU Pillow 12.3.0 / NumPy 1.26.4 / PDFium 5.14.0 decode: three passed, including
  PNG pixels/animation/corruption, scanned-PDF order and page/pixel limits. No GPU model ran.
- Host deployment manifest coverage, changed Markdown links, instruction parity, whitespace
  and the private public-safety scanner passed.
- A private 27-file hashed candidate archive and route-create preview are prepared.
  Credentials and private env files are excluded from the archive; deployment-specific
  hashes/configuration remain private. It is explicitly marked unqualified.

The broad run preceding the final gate had 24 failures in schema-version, host fixture,
page-enablement and subprocess mocks. All 163 affected regressions passed after their
fixtures were updated. These failures remain recorded, distinct from native qualification.
Live installation, real proxy requests, memory/accuracy/load and transitions remain pending
the [approval follow-up](../../../plans/archive/platform/20261030_paddleocr-rollout-qualification.md).


Subsequent release checks preserved the following outcomes:

- A repeated serial run reached 3,821 passed and 96.99% coverage, with one existing Linux
  watchdog fixture failure: the killed process disappeared between a liveness check and
  reading procfs. The fixture now accepts that confirmed absence. The final 119-test
  watchdog/OCR lifecycle/ownership/transport regression group passed.
- A package-gate regression showed that distribution names containing dots must normalize
  like their hyphenated lock names. Canonical-name and version-drift tests pass after that
  correction. A deterministic stop race also proves that a vanished process group is
  confirmed down before removing its receipt; unrelated/replaced owners remain protected.
- An optional four-worker run in a transient tool environment was interrupted after
  3,168 passed, 50 failed, four skipped and eight errors. Failures included fixture engine
  ownership becoming unknown. This experiment is not a release-gate pass; the canonical
  serial devcontainer command is authoritative. No live service was involved.
- Updated private candidate archives were rebuilt after the environment/cleanup fixes.
  Earlier private candidates remain intact; the current archive is still unqualified.

Final canonical serial release gate: **3,825 passed, four skipped; 97.24% coverage**.
Ruff and strict mypy passed on the final code. The final run includes the package-name,
process-disappearance and watchdog fixture corrections. Browser/SDK/CPU decode evidence
above remains separate from native model qualification.

Host-authenticated SSH was relayed into the devcontainer for the existing operations tools.
The read-only host deployment preview completed: 17 new and nine differing files, with
apply disabled. The current private release contains the matching host payload and explicit
DB rollback guidance. All changes are staged, no commit was made, and the approval request
for live rollout remains pending. No Mac service or LiteLLM route was changed.

## Subsequent native outcome — 2026-10-31

The [native rollout report](20261031-native-rollout.md) records the approved release and
bounded qualification. The original pending and failed observations above remain
historical; they are not rewritten by that later passing result.
