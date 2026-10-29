# Splash 128K context update

Both deployed Splash profiles now use **131072 total context tokens**, including prompt
and generated output. MLX remains healthy, selected and the default. Existing profile IDs,
prepared names, model aliases, gateway routes, key scopes and client credentials are
unchanged; their historical `32k` suffixes remain compatible. Profile labels show 128K.

| Profile | Configured window | Direct extended prompt | Direct near-limit prompt | Gateway near-limit prompt |
|---|---:|---:|---:|---:|
| stock-8bit | 131072 | 40252 | 129964 | 129964 |
| modified-4bit | 131072 | 40252 | 129964 | 129964 |

All six Splash requests completed with correct deterministic retrieval, reasoning, normal
stop and exact final prompt/completion usage. Both profiles reported matching loaded native,
admin and served-model limits plus valid memory plans under the unchanged 40 GiB allocation
budget. BF16 KV, FP32 state, reasoning on, disk cache zero, idle release off and the dedicated
endpoint remain unchanged. Pinned runtimes, weights, tokenizers and prepared receipts were
not rebuilt or replaced. Native child/controller cleanup and original service/intents
restoration passed; the original authenticated gateway worked before and after the window.

The launcher forwards the validated profile value, and both byte-identical profile parsers
accept legacy 32768 or 131072 only. Fingerprints change with the limit; readiness refuses
old context or model-list data. Historical 32K telemetry/comparison evidence is preserved.
The admin note now describes profile-derived limits instead of hard-coding 32K.

## Qualification and limits

The successful window used **8 of 12 inference requests**, with a 45-minute total limit and
final 10 minutes reserved for restoration. No client retries or memory-budget increases
occurred; deployed files stayed unchanged throughout native inference qualification. Long requests use
an explicit 900-second read/overall deadline. Both gateway near-limit requests follow direct
use of the same prompt, so this does not qualify cold gateway latency or arbitrary 128K
quality. Large prompts require enough client time for prefill. The frozen performance
comparison remains partial/inconclusive; MLX remains the default. Vision is unsupported.

Earlier packets remain unchanged: the first passed three requests but aborted on an
operator audit reading catalog metadata instead of loaded details. The second passed three
requests and retained a failed near-limit gateway timeout: its 120-second read timeout was
shorter than its 900-second overall deadline. Both restored matched 32K source/config and
verified original inference. A tested audit fix and aligned client timeout preceded the
fresh successful packet; no failed capture is rescored. An upload-only bytecode-cache mode
error occurred before the second window and had no inference/service effects.

Final repository gate: **3423 passed, 2 skipped, 98.14% coverage**. Standalone host suite:
335 passed; owning control and app Splash/profile contracts have 100% statement/branch
coverage. The host parser has 99.3%, with its identical app copy at 100%. Ruff and strict
mypy for 60 files pass. A full run exposed a pre-existing reservation-test synchronization
race; that test now observes guarded callback entry/release. Production locking is unchanged.
Earlier interrupted/failed full runs remain recorded. Instruction/parser parity, diff checks
and the private-pattern public scan are required before staging.

## Operation and rollback

Select the matching Splash profile in the admin, then use its existing scoped gateway
model. The context limit includes generated tokens; leave an output allowance. Existing
connection details and credentials remain private and valid.

Matched app/host/config and consistent schema16 backups are retained. The private reviewed
runner can restore original MLX/services, stop the admin only after native absence, apply
the matched previous source/profile files under the existing lifecycle reservation, and
resume/verify the prior configuration. It retains current schema16 data and all history;
never restore a live SQLite main file or erase ownership receipts to force activation.

See [structured evidence](context-128k-results.json), [implementation plan](../../../plans/archive/splash/20261029_splash-128k-context.md),
[host contract](../../../host/splash/README.md) and the preceding [32K rollout](rollout-report.md).
