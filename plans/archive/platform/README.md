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
- [Drain-before-switch and configurable inference admission](20261101_drain-before-switch.md): deployed with bounded live stream, cancellation, timeout and failed-readiness rollback checks.
- [Shared gateway request timeline](20261101_shared-request-timeline.md): implemented and deployed; [bounded live rollout](20261101_shared-request-timeline-rollout.md) qualified the shared gateway workload.
- [ComfyUI + Qwen-Image-2.1-Turbo](20261107_comfyui-qwen-image-2-1-turbo.md): int8 and GGUF turbo text-to-image plus a single-reference edit qualified on ComfyUI 0.37.0; engine restored to MLX. Follow-up is deferred.
- [Image studio](20261107_image-studio.md): template page (Turbo t2i/edit, Anima anime) with the admin's engine switching; enhancer encoders downloaded; 3-reference edit qualified; MFLUX 4-bit Turbo image produced.
- [Optional admin login](20261108_admin-auth-toggle.md): `ADMIN_AUTH_ENABLED=false` trusted-LAN mode; deployed and live-verified with auth disabled.
