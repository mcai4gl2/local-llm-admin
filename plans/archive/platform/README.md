# Platform archive

Closed implementation and investigation records. Task percentages describe their original
scope; incomplete experiment evidence remains incomplete. Current operations are documented
in [docs](../../../docs/DESIGN.md) and [host guides](../../../host/README.md).

- [PaddleOCR-VL and generic resident services](20261030_paddleocr-resident-services.md): local implementation evidence; original failed/partial outcomes preserved.
- [PaddleOCR live rollout and qualification](20261030_paddleocr-rollout-qualification.md): released with fixed task/load/memory and native lifecycle checks passed for one sharing model; broader scope deferred.

- [Task-quality evaluation and model scorecards (M10) Implementation Plan](20261017_task-quality-evals.md)
- [Local-model token usage (input / cached / output) Implementation Plan](20261022_token-usage.md)
- [Preserve log-rotation submission and completion state](20261025_log-rotation-completion-race.md)
- [MLX unload memory investigation](20261027_mlx-unload-memory.md)
- [Repair live inference after engine switching](20261029_engine-switch-load-failure.md)
- [Explain Strata request refusals](20261029_strata-request-errors.md): correction deployed; client investigation deferred.

- [Admin website consistency and usability](20261030_admin-web-consistency-and-usability.md): all 28 findings implemented, locally verified and deployed with the native engine preserved.
