Overall Progress: 100%

# Native collector live validation

Date: 2026-10-26
Status: Both checkpoint pilots are complete and the original service is independently
verified restored. The interrupted first stock series is retained separately. Accounting,
EOS, reasoning and cancellation checks pass; modified MLX overhead remains inconclusive.
Native speed stays unassessed. See the [report](../ops/splash-phase0/native-validation-report.md).

## Scope and constraints

Run stock 8-bit and selected modified 4-bit as separate comparisons and separate guarded
windows. Use isolated source copies for original and instrumented profiles, exact pinned
weights/draft/dependencies, loopback port, 32K context, existing precision/memory settings
and original-service restoration. Never patch the installed MLX environment or original
Splash source. This is collector validation, not a native speed qualification.

Each engine uses original, instrumented, instrumented, original launches. Each launch has
one excluded 64-token warmup and two repetitions of the frozen short/2K/8K prompts capped
at 256 output tokens. Instrumented launches additionally test natural stopping, reasoning
and cancellation. At most 56 timing/warmup requests and 12 functional requests per cohort,
each capped at 60 seconds, plus 12 minutes setup and 10 minutes restoration: 90 minutes.
Independent host guard: 110-minute collection cutoff, 120-minute restore deadline, existing
pressure/swap/headroom and process-generation controls. Failed attempts remain visible.

## Tasks

- [x] 🟩 Add failing trace/HTTP/input-association and overhead comparison tests.
- [x] 🟩 Implement strict review and cluster-bootstrap overhead analysis with private outputs.
- [x] 🟩 Prepare pinned original/instrumented source copies and freeze launch/request ledgers.
- [x] 🟩 Run tests, lint and typing; review live setup before activation.
- [x] 🟩 Execute stock 8-bit guarded validation and independently verify restoration.
- [x] 🟩 Execute modified 4-bit guarded validation and independently verify restoration.
- [x] 🟩 Publish results, limits, source/trace receipts and staging scanner outcome.

## Eligibility rules frozen before data

Every successful instrumented request must match its private native-input companion,
unique request nonce, actual final HTTP token count/reason and requested settings. Preserve
whole native batch boundaries. Natural-stop/reasoning/cancellation checks must pass; an
intentional cancellation produces an ineligible failed trace and must release the request
for subsequent real inference. Missing/extra/corrupt traces prevent qualification.

For each engine, pair original/instrumented requests within the two alternating blocks,
with matching input/response hashes, prompt counts, full output cap and zero reuse. Require
all expected samples; exclude only declared warmups. Report medians and 95% cluster-bootstrap
bounds over launch pairs and prompt categories. Collection overhead is eligible only when
the observed median and upper 95% bound are <=1.05 for the overall and every category.
An incomplete interval is inconclusive, not a pass. No cross-engine native speed verdict
is inferred from this pilot. Physical queue/transport differences remain part of the audit.

## Validation

Use the existing devcontainer and uv environment. Run focused native/tooling tests, Ruff,
strict mypy, instruction parity, whitespace and private scanner checks. Preserve the
completed evidence byte-for-byte. Append the devlog after changes. Do not commit.

## Recorded pilot interruption and separate follow-up

The first stock pilot retained all 34 attempted requests, including two intentional
cancellations and one final original-MLX 8K client timeout. Its two instrumented launches
accounted for all 18 successful native traces. The server logged completion near 50 seconds,
but the HTTP client lacked final accounting at its 60-second deadline. The complete-series
overhead decision is inconclusive; no failed sample is replaced or pooled with later data.
Original service, assets, configuration, intent and stopped sidecars were verified restored.

A separately frozen stock follow-up uses fresh nonces and trace directories. Only 8K
request deadlines increase to 90 seconds; other requests retain 60 seconds. The maximum
request/setup/restore budget is 98 minutes, inside a new independent 110-minute collection
and 120-minute restoration guard. This begins only after the modified pilot restores;
it does not extend either preceding window. Input/output caps, collector/source pins,
paired analysis, uncertainty and the 5% overhead criteria remain unchanged.

## User context confirmation

On 2026-10-26 the user confirmed that testing at 32K is sufficient for now. This pilot
retains the effective 32,768-token profile. Fresh near-limit context correctness and
resource tests remain separate from the pilot's short/2K/8K inputs.

## Modified 4-bit pilot result

All 68 requests completed as declared: 64 successful HTTP replies, four intentional
cancellations and no request errors. All 36 successful native traces are accounted; the
four cancelled requests produced the expected failed traces and released the request slot.
Both engines passed natural-stop and reasoning checks in both instrumented launches.
Original inference, admin, intent/configuration, target assets and sidecars were verified
restored within the approved window. The optional thermal observer started late; continuous
independent memory/pressure/swap guarding remained active.

Splash meets the predeclared overall and per-category 5% overhead criterion. MLX overall
median wall ratio is 0.9984 with 95% bounds [0.9883, 1.0276], but its 8K upper bound is
1.0764. Therefore MLX collector overhead remains **inconclusive**, without changing the
threshold, selecting samples or claiming a native speed qualification.

## Final result

The separate stock follow-up completed all 68 requests and accounted for all 36 successful
native traces; all functional and restoration checks pass. Both stock statistical overhead
decisions are eligible. Modified MLX's 8K uncertainty still prevents overall collector
qualification. Final audits cover 90 retained successful traces, 20 EOS natural stops,
24 isolated launch/import receipts, all four package inventories and 57 installed runtime
files. Source/EOS audit activity overlapping MLX segments is disclosed as a possible
CPU/disk timing confound. No samples are excluded or criteria relaxed.

This execution plan is complete with an inconclusive qualification result. Full native
speed and the remaining Phase 0 matrix are open under the parent plan. Temporary owned
SSH/agent transports are closed; host agent, installed source and production defaults are
untouched. Public-safety scanner outcome is recorded in the devlog; changes remain unstaged.
