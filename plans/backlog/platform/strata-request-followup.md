# Strata client request follow-up

Status: deferred, awaiting the failing client's exact error and whether image input is
required. The error-reporting correction is implemented and deployed; this is not a
pending source deployment.

## Next decision

Use a concrete failing request to distinguish unsupported images, long reasoning,
queued work and client timeout/retry behavior. Decide whether vision installation or
generation-policy changes are needed only after the intended client behavior is known.

During a future approved Strata window, confirm the recognized image refusal through
the shared endpoint. The latest deployment preserved the selected MLX engine and checked
the installed translation with a representative upstream response instead.

Preserve the user's requests and engine selection. Do not install vision assets or
change reasoning, output or retry policy as part of this deferred investigation.

See the [partial investigation](../../archive/platform/20261029_strata-request-errors.md),
[operating guide](../../../docs/shared-inference.md) and
[deployment record](../../../docs/reports/latest-code-deployment-20261030.md).
