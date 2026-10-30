# Operations

Reusable deployment and maintenance tools live here. Start with the
[host guide](../host/README.md) and [Splash operating guide](../host/splash/README.md).
Targets come from the ignored `deploy.env` or environment, never tracked host details.

| Task | Tool |
|---|---|
| Inspect host drift | `host-diff.sh` |
| Preview / apply the versioned host manifest | `deploy-host.sh` / `deploy-host.sh --apply` |
| Deploy / roll back the admin | `deploy-mac.sh` / `rollback-mac.sh` |
| Resolve private deployment settings | `deploy-env.sh`, `deploy.env.example` |
| Review admin service configuration | `env.example`, LaunchAgent template |
| Maintain the original ComfyUI exclusion patch | `apply-mlx-model-guard.sh`, `patches/` |

Run repository commands in the devcontainer as `vscode`. Read-only drift and dry-run
checks do not change services; applying a deployment or rollback requires explicit live
approval. `deploy-host-remote.sh` is an implementation helper, not a separate entry point.

Experiment tools and their reproducibility inputs are under [experiments](../experiments/README.md).
Historical Splash rollout reports and bounded qualification evidence are under
[docs/reports/splash](../docs/reports/splash/INDEX.md). They are records of their original
revision/window, not instructions to rerun those windows or evidence for later changes.

Append-only historical devlogs may cite the old `ops/*-phase0/` or root `plans/` paths.
Use the experiment and plan indexes above to find the moved bundle; old JSON source
labels intentionally retain the original capture locations.
## OCR release preparation

The [resident OCR runbook](../host/ocr/README.md) describes isolated provisioning, paired
private configuration, per-route rollback and native qualification. Generate a reviewable
private release archive with `uv run python -m local_llm_admin.ocr_release`; preview the
LiteLLM route with `uv run python -m local_llm_admin.ocr_route`. Both run locally in the
devcontainer; route preview makes management reads only. Code deployment, native package
installation/model downloads, configuration changes, service starts/stops and route writes
belong to a separately approved live window. Use configured `MAC_HOST`/`MAC_DEST` targets.
The host allow-list includes the OCR code and both hashed environment locks. Keep deployment
backup manifests, provisioning receipts and route snapshots private for rollback.
