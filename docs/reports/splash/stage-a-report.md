# Splash Phase 3 — Stage A deployment report

Date: 2026-10-28
Outcome: **Stage A deployed and verified. Splash remains disabled.**

## Approved scope and result

The user approved offline preparation of the original pinned Splash runtime and two
independent asset/assembly profiles, followed by the paired host/admin deployment and
schema16 migration. MLX remains the default. The original MLX process identity, model
selection and engine/sidecar intents are preserved. No Splash serving process was launched,
and the existing gateway configuration/public route was preserved.

The runtime archive and target/draft bytes were checked against the frozen identities.
Both assemblies passed full offline verification, and final preparation receipts bind
their actual resolved outputs. The modified tokenizer overlay is explicitly retained;
its base Git revision alone is not presented as the full checkpoint identity.

## Verification evidence

| Check | Result |
|---|---|
| Fresh target/payload/process/intent/config/jobs/disk preflight | Pass |
| Existing authenticated gateway route before deployment | Pass |
| Independent stock/modified assemblies and final receipts | Both pass |
| Matched pre-migration SQLite backup | Schema15; integrity `ok` |
| App, virtual environment, Python runtime, env, LaunchAgent and whole host backups | Present and verified |
| Paired host/admin deployment and deployed file hashes | Pass |
| Current database | Schema16 |
| Admin health and authenticated operator session | Pass |
| Original MLX PID/create-time/executable and selected model | Preserved |
| Original engine/sidecar intents | Preserved |
| Existing authenticated gateway route after deployment | Pass |
| Gateway configuration digest | Unchanged |
| Host code drift check | Pass; registry state preserved |
| Splash flag / owned serving processes | Disabled / zero |
| Native snapshot coverage and initial row counts | Disabled; zero snapshot/availability rows |
| Temporary gateway routes/key | Not created |
| Rollback needed | No |

Two original-route verification attempts were used from the four-attempt cap. Each
request had a 32-output-token cap and 120-second deadline; reports retain status/counts
without prompts or response text. These checks establish connectivity, not a latency,
throughput, quality or monitoring-overhead comparison.

The cutover from writer-backup start through the successful post-change route check
completed in approximately 72 seconds, within the
five-minute allowance. The ten-minute restoration reserve was not used. Named matching
backups are retained for at least seven days and through the rollout decision; app.prev
is not the database/schema rollback mechanism. The temporary host-agent relay was closed.

## Deployed source provenance

This deployment uses the approved working-tree snapshot on Git base
`c25078cea25a64be95441709314f7a966f7b5857`. No commit was created.
The digests below cover public code manifests, excluding private env/credentials:

- App source manifest SHA-256: `b08bd762b4c58c9c93abb9563f90b0eab5e103354af8b7050a01f5708ee428ce`
- Host code manifest SHA-256: `fc49f131571dbc5d90cccc353d08b234d300a6511a65fb7ddef7c857354bb9c6`

The immutable approved patch, payload/review digests, actual installation receipts,
backup paths and detailed execution evidence remain in the ignored private deployment
worksheet. Historical approval/readiness snapshots are preserved separately from live
execution outcomes.

## Validation and completion limits

The same application/host runtime source passed the existing 3,306-test regression
checkpoint (two skipped, 98.14% coverage), plus 34 browser checks. Deployment preparation
also passed 11 synthetic operational rehearsals, 47 database/deployment tests, Ruff,
strict mypy and public-pattern scanning. Actual target assembly, migration, process
preservation, health and existing-route checks above are additional live evidence.

A live database/application rollback was not exercised because deployment succeeded.
The matched backup is integrity-checked, and offline rollback tooling has automated tests.

Stage A is installation and disabled deployment, not Splash production qualification.
Owned Splash serving transitions, both-profile direct/gateway streaming, required 32K,
auth/Host refusal, cancellation, counter-reset/population behavior and integrated overhead
measurements remain pending their separately approved window. Frozen Phase0 failures,
inconclusive results and unassessed gates are unchanged. Production adoption and any
change of default remain a separate decision.
