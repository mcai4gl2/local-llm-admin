# Splash Phase 3 — Stage B functional qualification

Status: **prepared, not executed; live approval required**.

Stage A installed the pinned runtime, independent profiles and schema16 application with
Splash disabled. This window temporarily enables Splash and verifies owned lifecycle,
both-profile HTTP compatibility and telemetry attribution. MLX remains the default.
The [request contract](qualification.json) is separate from the unchanged
[performance design](measurement.json). Private paths, route bodies and credentials are
resolved in `.private/splash-phase3/stage-b/REVIEW.md`.

## Limits and request order

One 45-minute window includes a final ten-minute restoration reserve. Concurrency is one;
every inference attempt, including refusal, cancellation, failure and retry, counts toward
40. Requests have a 120-second total deadline. Scheduled traffic is 29 attempts; the
remaining 11 are available only for reviewed retry or restoration. Stop dispatching new
qualification work at minute 35, and sooner if a request would consume the reserve.
Normal requests allow at most 2,048 output tokens. Original-route checks allow 32.

Before switching, capture the original authenticated gateway inference and a fresh
schema16 online backup plus env/LaunchAgent/config/intent/process baseline. Enable the
reviewed environment, restart only the admin, and create two isolated test routes and
one two-hour restricted key through the existing gateway management API. Preserve the
original route and gateway file configuration.

For each profile, run these 13 attempts in order:

1. Direct and authenticated gateway streams: require correct model identity, reasoning,
   content, finish reason, EOF and final usage; retain missing fields and failures.
2. One direct and one gateway near-limit context smoke, using the actual pinned tokenizer
   and chat template. Require 31,744–32,000 rendered prompt tokens plus at most 512 output
   tokens within 32,768. Verify the synthetic middle-position retrieval answer. A short
   request to a 32K-configured model does not qualify this check. Truncation remains fail.
3. Direct invalid auth, unknown model and invalid Host; gateway invalid auth and unknown
   model. Require explicit refusals without generation; unexpected success aborts.
   A restricted gateway key may refuse an unknown name by model policy; record that
   boundary separately from direct upstream alias refusal.
4. Direct cancellation after the first nonempty stream emission, then direct success;
   gateway cancellation after emission, then gateway success. Observe runtime idle and
   unchanged owned generation after disconnect; follow-up success alone cannot establish
   cancellation propagation. If cancellation is unobservable, record inconclusive.

After stock checks, switch off and verify every owned member/listener is gone, then
restart stock and make one direct follow-up request to verify a distinct telemetry epoch.
Switch from stock to modified through the normal admin job API. Verify fingerprint and
generation separation before testing modified. End with an owned stop, original explicit
MLX/service restoration, disabled environment, temporary route/key removal and original
authenticated gateway inference. Restoration expects a new MLX process generation.

## Telemetry and platform evidence

At readiness, after request groups, after stock restart, after profile switch and after
stop, save authenticated Performance, availability and metrics exports plus owning-wrapper
status. Check matching profile/fingerprint/controller/native identities, fresh readings,
no duplicate snapshots for unchanged counters, independent epochs, no folded MLX request
totals, intended-time availability and complete/partial bundle metrics. Prime CPU before
reading it; summed RSS remains a process sum, not physical memory reclamation.

Use the actual macOS process/executable/create-time and listener inspection. Check all
GPU services are stopped before Splash starts, lifecycle locks release, and stale owners
never authorize a signal. Abort on unknown ownership, misattribution, missing refusal,
critical pressure, sustained swap growth, less than 6 GiB available or exhausted reserve.
The request client checks resource/ownership guards before dispatch and every five seconds
during Splash HTTP traffic. Read-only status cannot prove forced-crash recovery or PID-reuse
behavior.

## Restoration

Use the owning stop even if telemetry fails. Confirm every recorded/rediscovered generation
and listener is absent before restoring another engine. Refuse to kill an unknown process.
Restore the original explicit MLX selection and service running states through normal
control APIs, then restore the saved disabled schema16 environment and LaunchAgent.
Verify semantic intents, health, selection, gateway inference and cleanup. Preserve new
telemetry records in schema16; this window does not migrate or restore the database.
The Stage A matched schema15/app backup remains a separately reviewed deployment fallback,
not the normal Stage B cleanup command.

Remove only the temporary key/routes whose ownership is recorded. Preserve evidence of
partial/ambiguous creation for cleanup. If cleanup or source restoration fails, stop
qualification and report the unresolved state. Never silently expand the window.

## Completion limits

Publish pass/fail/inconclusive/unverified per check after execution. Preparation establishes
no live Splash result. Forced child failure/recovery, process-inspection fault injection,
admin current-only compatibility/eval acceptance and the frozen performance comparison
remain separate work. This window is no production adoption decision and changes no
frozen Phase0 result.
