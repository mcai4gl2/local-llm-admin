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
