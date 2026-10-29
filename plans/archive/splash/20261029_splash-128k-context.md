Overall Progress: 100%

# Splash 128K context update

## Summary
Raise both deployed Splash profiles to 131072 total context tokens while preserving
existing profile IDs, prepared assets, gateway routes, client credentials and MLX default.

## Scope and constraints
Retain strict 32768 compatibility and add the requested 131072 serving limit. Launch uses
the validated per-profile limit; ownership fingerprints and readiness remain exact.
The existing IDs/prepared names may retain their historical 32k suffix; labels and limits
advertise 128K. Keep BF16 KV, FP32 state, reasoning on, 40 GiB allocation budget, disk cache
zero, idle release off, dedicated endpoint and all lifecycle guards. No runtime/model
installation, benchmark reset, broad adoption claim, memory-cap increase or database restore.

## Tasks
- [x] 🟩 Add failing host/admin profile, launch and readiness tests for 128K.
- [x] 🟩 Implement validated profile limit forwarding and update current examples/guides.
- [x] 🟩 Pass focused and repository quality gates.
- [x] 🟩 Back up matching deployed files/config/schema, apply under lifecycle reservation,
  qualify both profiles through direct/gateway inference above 32K, and restore MLX/services.
- [x] 🟩 Record exact tested limits, rollback, results, public scan and staged changes.

## Validation and performance
Run focused Splash tests first, then `uv run pytest --cov --cov-fail-under=80`, Ruff and
strict mypy in the devcontainer. Read-only live preflight checks source, native parser,
model context support, memory, jobs/locks, original ownership and gateway routing.
After a matched backup, deploy both parser copies and launcher plus private profile data.
Use one bounded 45-minute live window with the final 10 minutes reserved for restoration,
at most 12 inference requests, no automatic request retries or memory-budget increases.
For each profile, verify loaded native/admin/model-list context 131072, valid memory plan,
complete direct/gateway streams and an actual prompt beyond the old 32768 limit; attempt a
near-128K retrieval prompt only if the first larger-context request and memory checks pass.
Retain partial/failure evidence. A successful capacity setting is separate from full-window
quality or throughput qualification. Report exact prompt tokens and any unassessed boundary.

## Operations and rollback
Capture current source hashes, private profiles/receipts, admin env/plist, original
selection/intents/services and consistent schema16 snapshot. Stop only known admin/native
ownership; install matched files atomically under the existing engine reservation and
restart admin. Restore MLX and baseline services/intents after testing. If activation or
inference fails, preserve evidence and restore the matched previous source/profile config;
retain all new database history. Never kill unknown owners or remove ownership receipts.

## Integration and observability
Host/std-lib admin profile copies, owning launch, loaded status/model lists and profile
fingerprints are the contract. UI labels and max_context flow from private profiles.
Gateway routing and key scopes stay unchanged. Retain source/generation/memory/context and
request usage captures privately; publish only sanitized outcome evidence.

## Open questions
None. The existing session approval covers the deployment; no commit is authorized.

Validation found an existing reservation test raced acquisition: owner-file creation
precedes release of the maintenance gate. The test now waits for guarded callback entry
and explicitly releases that callback, preserving the production locking contract.
The first failed full attempt remains recorded. The final full suite and independent
Mac functional qualification may run concurrently after focused tests and lint/types pass.


## Validation record
Final repository suite: 3423 passed, two skipped, 98.14% coverage. Splash client and
app profile contract have 100% statement/branch coverage. Standalone host suite: 335
passed; owning control has 100% statement/branch coverage, host parser 99.3% (its
byte-identical app copy is 100%). Ruff and strict mypy pass for 60 files.
The first full attempt was interrupted to include the UI note correction; a later
attempt exposed the reservation-test race described above. Preserve both partial logs.

First live packet: three requests passed, including stock direct 40252-token retrieval;
a wrong admin audit field aborted the packet and matched 32K source/config restoration
passed. Second packet: three requests passed and one near-limit gateway stream timed out;
the client had a 120-second read timeout despite its 900-second overall deadline. Matched
rollback and original inference passed. Neither packet qualifies unattempted conditions.
A regression test aligns client read/overall deadlines. The third packet retains the
45-minute/final-10-minute/12-request bounds, adds direct near-limit inference before the
same gateway prompt, and preserves all earlier failed/partial records. It completed successfully.


## Completed deployment
Both profiles are enabled at 131072 total tokens with unchanged serving identities,
credentials, assets, precision and 40 GiB budgets. The third window passed all eight
inference requests: original gateway before/after, and for each profile direct 40252,
direct 129964 and same-prompt gateway 129964 token retrieval. Reasoning, normal stop,
exact usage, loaded context/model lists, valid memory plan and original restoration pass.
Source stayed unchanged during native qualification. No owning resources, pending jobs
or lifecycle locks remain. Schema16 integrity is `ok` and all history is retained.
See the [report](../../../docs/reports/splash/context-128k-report.md) and
[structured results](../../../docs/reports/splash/context-128k-results.json).
Further cold-gateway latency, broad 128K quality and benchmark/adoption remain unassessed.
