# PaddleOCR resident runtime

This opt-in Apple Silicon runtime passed the approved bounded qualification for one
configured chat model alongside embeddings. The [native report](../../docs/reports/ocr/20261031-native-rollout.md)
records its exact scope, measured memory/load, preserved failures and limitations.
The full layout/document pipeline runs in a persistent terminable process; a separate
persistent MLX-VLM server recognizes its regions. The loopback API imports no native OCR
packages. See the [API guide](../../docs/ocr.md). New deployments remain unqualified until
measured on their own hardware and settings.

Candidate pins:

| Component | Pin |
|---|---|
| PaddleOCR / PaddlePaddle | 3.6.0 / 3.2.1 |
| MLX-VLM / MLX / MLX Metal | 0.7.3 / 0.32.2 / 0.32.2 |
| Python/platform | CPython 3.12, macOS 14 or later, arm64 |
| PaddleOCR-VL-1.6 | `c5630abae1d940eafe0697512a0325494b02ab42`, BF16 |
| PP-DocLayoutV3 | `241f8bdfc77a7c7bee915a5057aaee58c235a8d3` |
| PP-LCNet document orientation | `d3b95a6dff5fe8a94f2748e12b61cb26818a0df8` |

All three model repositories advertise Apache 2.0. The layout snapshot uses Paddle
`inference.json`, `inference.pdiparams`, `inference.yml`; VLM weights use safetensors.
The current candidate enables the pinned CPU orientation classifier and maps boxes back
to original input pixel coordinates. Only the orientation classifier receives an exact-white-margin-trimmed view with padding,
keeping every non-white pixel. Layout and recognition receive the full rotated page.
Unwarping remains disabled. Clearly separated text
columns are read down each column; ambiguous layouts retain upstream order. Plain text
removes whole-block math presentation delimiters while Markdown preserves them. The initial
orientation-disabled candidate failed three corpus tasks; its [scores](../../docs/reports/ocr/20261031-initial-corpus.json)
remain frozen. The new candidate must pass the same thresholds.

`environment/ocr-app.txt` and `environment/ocr-vlm.txt` are separate complete, hashed
candidate locks. They were resolved for macOS 14 arm64, not executed on Linux. Reproduce:

```sh
env MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile host/ocr/etc/requirements.in \
  --python-version 3.12 --python-platform aarch64-apple-darwin --only-binary :all: \
  --generate-hashes --no-header --no-annotate --quiet -o host/environment/ocr-app.txt
env MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile host/ocr/etc/vlm-requirements.in \
  --python-version 3.12 --python-platform aarch64-apple-darwin --only-binary :all: \
  --generate-hashes --no-header --no-annotate --quiet -o host/environment/ocr-vlm.txt
```

Installation layout under the private OCR root:

```text
app/ bin/ etc/runtime.json
venv/ venv-vlm/ models/<name>-<revision-prefix>/
cache/ logs/ run/ tmp/
```

Before asking for a live window, prepare a private candidate env from the current Mac
settings. Choose unused ports from read-only topology checks, explicit compatible MLX
names and a provisional reservation. Do not copy credentials into the repository.
Build the reviewable bundle in the devcontainer:

```sh
uv run python -m local_llm_admin.ocr_release --env-file .private/ocr-candidate.env \
  --output .private/ocr-release.tar.gz
```

The archive is mode 600, includes file hashes, pinned locks, code, a normalized catalog
and runtime config, and omits the env file/credentials. It is marked `qualified: false`.
Rebuild after any payload/settings change. Do not blindly extract it over a live volume.

An approved rollout must:

1. Save a private copy of the admin env, current resident catalog/runtime config and
   SQLite database using a consistent SQLite backup. Record current desired intents.
   The service request migration is additive v17; leave old embedding tables intact.
2. Review `ops/deploy-host.sh` dry-run for the exact bundle code paths. Deploy with
   `--apply` only in the approved window; keep its per-file backup manifest. Install the
   private catalog and runtime config atomically at mode 600, and align admin env settings.
   Keep the catalog and settings paired; new starts refuse drift. No other runtime venv
   is upgraded. Deploy/restart the admin using its existing deploy/rollback procedure.
3. Preview `/Volumes/Models/ocr/bin/provision-ocr`. For first installation, explicitly run
   `provision-ocr --apply --download-models --receipt <private-receipt-path>`.
   It holds the same lifecycle lock, refuses live/orphaned OCR owners and existing
   candidate directories, creates only the two OCR venvs and three pinned snapshots,
   validates package pins and receipts, and starts no model. Preserve failures.
4. Start OCR through Models. Its wrapper checks aggregate policy, starts VLM before API,
   and runs functional `12345` recognition before readiness. It never starts on a request.
   Measure OCR peak before finalizing its reservation. Validate direct image/PDF results,
   fixed corpus scores and joint chat/embedding impact before enabling client traffic.
5. Preview the LiteLLM route, approve/apply it with a private route receipt, then make
   real image and PDF calls through the proxy. Exercise suspend/restore, active-work
   refusal/force and failed dependency cleanup in that same approved window.

Use `paddleocr-vl status` for liveness/weights. Stop checks exact PID, process group,
command and start time before signaling, in API/pipeline/VLM order. An orphan or stale
generation refuses admission; never kill an unrelated listener to make a start pass.
Admin start/restore jobs pass a unique start token. Failed-job cleanup uses that same
token and refuses to signal a replacement started by another caller. Legacy embedding
start refusals skip outer cleanup; their wrapper owns cleanup of its own failed launch.
Cancellation can intentionally lose readiness; use Stop then Start after ownership is
verified. Automatic crash recovery remains off.

Rollback reverses the approved steps: restore/delete only the OCR LiteLLM route using its
receipt; stop OCR and verify all components down; run
`provision-ocr --rollback --receipt <same-private-receipt-path>` to remove only directories
created by that receipt; restore paired private env/catalog/config and the host backup
manifest. A changed directory owner or route refuses automatic rollback. Keeping the v17
admin with OCR disabled preserves all histories and existing clients during a fallback.

An older v16 admin cannot open a v17 database. A code rollback therefore needs the matching
pre-deploy DB snapshot: stop the admin and release SQLite WAL connections, then use
the admin environment's `python -m local_llm_admin.db_snapshot restore --expected-version 16 --offline` with explicit
`--snapshot`, `--database` and a new private `--preserve-current` path. Preserve the
post-rollout DB separately; restore/merge its later unrelated history before declaring
history-preserving rollback complete. Never overwrite that preserved copy. Recheck
embedding/rerank and main chat. Never delete shared model caches or other environments.

An explicit Stop for a generation-checked service also runs scoped cleanup when its
processes have already exited, so stale generation records do not block provisioning.
A failed stop can be retried after verifying that ownership has not changed.

OCR regions run serially and yield between calls, including across documents. The idle
interval equals the preceding region-call duration, capped at one second. This trades
OCR throughput for interactive-model capacity; the existing page/document deadlines
include pacing. Admission is rechecked after waiting, so cancelled work cannot resume.
