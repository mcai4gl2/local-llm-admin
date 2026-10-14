Recorded 2026-09-29 on the Mac mini (macOS 27, read-only commands) for Phase 0.
`download-models.log` is the head and tail of the real file joined; `mlx-model-status-stopped.txt` is
derived from `cmd_status` in `mlx-model` (the server was running during recording).
`mlx-model.dc1` is `mlx-model.orig` plus the D-C1 `:8188` guard, as deployed on 2026-09-29: the
legacy CLI (no lock owner, no maintenance gate) that the first `ops/deploy-host.sh` run upgrades.
