# Splash Phase 3 — operating and approval worksheet

Both deployed profiles now use **128K (131072 total tokens)**. Existing profile/model
names and credentials remain valid; labels show 128K. Direct and gateway requests of
129964 prompt tokens passed on each profile. See the [128K update](context-128k-report.md)
and [structured evidence](context-128k-results.json). Earlier 32K reports retain their scope.

Supported Splash functionality is deployed and enabled for both profiles. MLX remains the
selected default, with original services/intents and existing route/key scopes preserved.
Two authenticated database-backed profile routes use a dedicated Splash endpoint; inactive
calls return unavailable while the exact original MLX generation stays healthy. See the
[final rollout report](rollout-report.md) and [structured verification](rollout-results.json).
The preceding 32K rollout qualified four endpoint lifecycle cycles, all twelve directed
engine-pair switches, both native/admin recovery windows and 18 commissioning cases.
Its schema16 snapshot had integrity `ok`, 85 native readings and 109 availability observations;
monitoring continued afterward. Final 128K restoration also verifies no owned native resources,
pending jobs or lifecycle locks. Connection parameters and client credentials remain private.
The [frozen comparison](integrated-comparison-report.md) remains partial/inconclusive;
preferred-backend adoption is deferred. The [endpoint correction](../../../plans/archive/splash/20261029_splash-endpoint-isolation.md)
prevents Splash aliases from reaching MLX's model-loading API, without adding a proxy or
patching installed inference runtimes. All activity used existing explicit session approval.
Earlier [shutdown](shutdown-fix-report.md), [readiness](stage-b-report.md) and
[gateway identity](readiness-fix-report.md) failures remain historical evidence; the
[29-case HTTP qualification](gateway-contract-report.md) remains valid within its scope.
Use [the plan](../../../plans/archive/splash/20261028_splash-phase3-telemetry-qualification-and-rollout.md),
[telemetry contract](../../splash-telemetry.md), [host guide](../../../host/splash/README.md)
and the ignored `.private/splash-phase3/worksheet.md`. Resolve every operator input
before presenting an approval request; a template is not an executable live window.

## Repository behavior and limits

- Splash remains disabled by default. `SPLASH_POLL_INTERVAL_S` defaults to 15 seconds
  (5–300); one collection may be outstanding. Disabled mode schedules no Splash HTTP.
- `/ready`, `/status`, `/v1/models` are bounded to the existing per-document 1 MiB cap
  and probe timeout. The independent monitor has an overall wait budget of twice the
  probe timeout; a late worker is retained, never replaced by an overlapping worker.
  An expired HTTP result cannot publish success. Timely readiness remains valid if
  storage alone times out; that timeout is reported separately.
  Monitoring cannot delay the supervisor/lifecycle loop. Cached readiness expires
  after three polling intervals and must match the supervisor's owned generation.
- Schema **16** adds only `engine_readings` and `backend_availability` plus indexes.
  Existing inference rows, daily token folding and MLX availability are unchanged.
  Native readings bind source/profile/fingerprint, controller instance and PID/create
  time, native PID/create time, and restart count. Reads across a generation change,
  stale native status or unknown ownership are refused.
- Counters are cumulative execution observations, not billed/request token totals.
  No all-client request history, response-body collector, reconciliation or final-stop
  capture is qualified. Query `/api/performance/engines` for JSON export; filter with
  `profile`, `fingerprint`, `from`, `to`, and `limit` (1–500). Never sum snapshots.
- Successful poll, last changed/ingested data, stale/error, and bounded gap history
  are separate. Regressed counters are refused; a new epoch starts a new baseline.
  Cursor/rows commit atomically. Cursor history is bounded to 128 epochs and 32 gaps;
  historical records remain queryable within retention. Snapshot retention prunes at
  most 50,000 rows daily; availability prunes at most 5,000 per sample. Both use the
  configured observability retention. Queries are bounded, including availability's
  20,000 samples; truncation is explicit, with uncovered time unknown.
- `/api/availability` retains MLX semantics. `?source=splash` is a separate denominator:
  loaded serving / observed intended time. Released/recovering are non-serving;
  deliberate off and jobs/maintenance are excluded. Missing observations are unknown.
  Profile/fingerprint breakdowns remain separate; nothing predating collection is
  silently treated as downtime. Availability sampling starts only after opt-in/intent.
- Diagnostics/alerts reuse cached readiness and registry labels. No Splash admin
  restart/watchdog is introduced. Telemetry storage failure does not invalidate a
  successful serving-readiness observation. Unknown observations cannot claim success.
- Process bundle RSS/CPU verifies each member before/after reading and excludes reused,
  missing or denied processes. Partial totals are null with observed-member detail.
  Summed RSS can count shared pages twice; it is not physical/unified memory usage or
  evidence of host reclamation. Legacy primary-process measurements remain available.

## Read-only preparation

Run project preparation in the devcontainer. When its SSH agent cannot authenticate,
the user-authorized host SSH identity can perform this read-only inventory. Preserve
output privately:

```bash
. ops/deploy-env.sh
require_deploy_env MAC_HOST MAC_DEST
mkdir -p .private/splash-phase3
chmod 700 .private/splash-phase3
ops/host-diff.sh > .private/splash-phase3/host-diff.txt 2>&1
ops/deploy-host.sh > .private/splash-phase3/deploy-dry-run.txt 2>&1
```

A failed SSH read is **unverified**, never proof a deployed file is missing. Record
actual deployed SHA-256s, app revision, schema version, LaunchAgent/env paths, current
intent/profile/services, verified process bundle, runtime/asset receipts, headroom,
and exact gateway public model versus upstream model, Host and both auth-file paths.
Do not copy credentials into commands/argv, reports or tracked files. Snapshot hashes
and private file paths belong only in the private worksheet. Record the final staged
repository diff digest/revision before approval; invalidate approval if it changes.

## Backup and database rollback

The old app refuses schema 16. `ops/rollback-mac.sh` alone cannot roll back this schema.
Use the matching pre-migration backup and app/environment/LaunchAgent/host/route bundle.
`app.prev` can be replaced by another deployment, so retain a separately named copy of
all app and host backups for at least seven days and through the rollout decision.

The stdlib-only `src/local_llm_admin/db_snapshot.py` works without importing/migrating
the app database. It uses SQLite's online backup API, including committed WAL pages,
integrity checks, mode 600, and atomic no-overwrite publication. Example commands on
an approved target, with **operator-resolved private paths**:

```bash
python db_snapshot.py backup --database "$ADMIN_DB" --output "$PRE_DB"
python db_snapshot.py restore --snapshot "$PRE_DB" --database "$ADMIN_DB" \
  --expected-version "$PRE_SCHEMA" --preserve-current "$FAILED_DB" --offline
```

Use the reviewed interpreter and copied helper path from the private worksheet;
`python` above is notation, not an approved Mac interpreter selection. Before a fresh
schema migration: freeze new jobs, stop all admin writers cleanly, verify stopped
LaunchAgent/processes, then make and validate the consistent pre-schema backup. A live
online backup is safe but its later writes would still need preservation on rollback.
The restore command refuses active WAL/SHM/journal companions. Do not delete those
files manually: cleanly stop/checkpoint the **displaced** database, preserve a consistent
post-window backup, then restore the old snapshot. Never copy only a live `.sqlite`.

The automated migration/backup tests cover a failed v16 step with no partial table,
repeated upgrade, WAL-backed backup, version refusal and restoration usable by a
schema-15 app. These are local SQLite tests, not a Mac deployment rehearsal.

## Prospective deployment order (approval required)

1. Freeze the exact final app revision/diff and **whole** host code manifest, including
   shared `bin/engine-guard.sh` and reciprocal MLX/Strata/ComfyUI/decision/TTS/embed
   wrappers. Existing host deploy preserves private profiles/env/receipts/runtime/assets.
   Audit/install those operator inputs separately; versioned deployment does not do it.
2. Back up the app, environment, LaunchAgent, host manifest/files and gateway route/auth
   settings to the worksheet's timestamped paths. Capture original authenticated
   inference, engine/profile/intent/services and DB schema. Stop admin writers and make
   the consistent pre-migration backup above before booting the v16 app.
3. In the approved quiet window, apply the reviewed paired host/admin deployment. The
   reviewed commands are `ops/deploy-host.sh --apply` and `ops/deploy-mac.sh`, resolving
   their target through deploy-env. They are not authorized by this document. Verify
   schema 16, `/healthz`, login, disabled default and original inference first.
4. Opt in via the private admin environment, using the pinned original Splash 1.4.0,
   two separately receipted profiles, 32K, reasoning on, BF16 KV, FP32 state, zero disk
   cache, idle release off, and no GPU sidecars. Verify owned start/stop/profile/off
   transitions, native recovery and generation changes, locks, real macOS executable/
   creation-time queries, cancellation and full process/listener cleanup.
5. Exercise direct **and actual authenticated gateway** streaming for both profiles,
   unknown model, invalid auth, Host refusal, final usage/reasoning/content, disconnect
   cancellation and a subsequent successful request. A gateway absolute MLX path cannot
   be a Splash alias; any upstream alias change must be separately reviewed in the
   concrete route diff. Never replace the public route silently.
6. Run only the frozen [integrated measurement design](measurement.json). Retain every
   failure/partial attempt and unresolved token count. No inference benchmark traffic is
   authorized during preparation. The original Phase 0 reports remain unchanged.

## Abort and restoration

Reserve the final ten minutes of each 30-minute window for restoration. Abort new work
on an unknown/reused generation, ownership ambiguity, sidecar conflict, missing auth/
Host refusal, excessive memory pressure, telemetry misattribution, failed cleanup,
failed schema backup or exhausted time reserve. A telemetry outage alone must not block
an owned stop; preserve its gap. Do not increase memory/cache/context or add recovery.

Stop Splash through its owning wrapper and verify every recorded/rediscovered member
and listener is gone. If resources remain unknown, stop and investigate; never start
another engine over them. Stop the new admin writer; preserve the post-window DB before
restoring the reviewed old-schema snapshot (post-backup records are absent from the
restored DB, but retained in the private failed-window backup). Restore the matching
app/environment/LaunchAgent, full reciprocal host/guard backup set and route/auth
snapshot, using the exact worksheet paths. Reload the old environment/interpreter,
restart the original explicit profile/services through owning commands, restore intent,
and verify authenticated gateway inference, health, fingerprints, locks and helper/
listener cleanup. A health-only check does not establish restoration.

Every live row remains pass/fail/inconclusive/unverified until measured. Present a
separate production decision with monitoring/stop criteria and exact route/config diff;
repository tests never opt in or change MLX as the default.


## Prepared deployment packet

The ignored `.private/splash-phase3/deployment/REVIEW.md` contains the concrete Stage A
command order, target-derived paths, exact source/overlay manifests, private auth-file
references, paired deployment diff, backups and recovery commands. The immutable packet
was approved and executed for Stage A; its original readiness statements are historical.
Current outcomes are in the private execution record and public deployment report. The private payload includes immutable app/host archives, profile
and receipt templates, disabled/enabled env candidates and guarded operator helpers.
Final installation receipts are emitted only after actual independent assemblies verify;
prospective templates are never presented as proof of installation.

Stage A installs the original pinned runtime/assets and deploys the paired host/admin
code with Splash disabled. Existing MLX generation, selection and intents are checked
before/after. Separate app, virtual environment, Python runtime, env, LaunchAgent, full
host manifest and schema15 backups make restoration independent of app.prev. A failed
backup can resume the original writer before deployment; a later failure requires the
matched schema/app/host rollback and retained displaced database. Unknown ownership,
changed inputs or remaining journal companions refuse restoration.

The pinned release's public `install.local.prepare_local_model` interface accepts explicit
local target/draft directories and makes no Hub request. Preparation blocks network use,
verifies complete assembly hashes and emits separate profile receipts. Inspection workers
have a deadline and their own process group so a timeout also cleans up owned children.
Private modes, no-overwrite behavior, payload/workspace digests and source/target identity
checks are rehearsed locally. Actual Mac assembly, migration and restoration remain live
verification tasks. Container project commands can use a temporary relay to the existing
host SSH agent without copying private keys; close the relay after the approved window.

Stage A permits at most four small original-route verification attempts including rollback
checks, each capped at 32 output tokens and 120 seconds. A durable reservation counts failed
attempts too; reports omit prompt/response text. Asset copies precede the admin outage.
The packet reserves five minutes for cutover and ten for restoration. No Splash serving,
temporary gateway route/key creation or integrated benchmark is part of that approval.

The separately prepared gateway management bodies add only two named test routes and one
restricted two-hour key, without restarting the shared gateway or replacing its public
route. Their apply/remove commands require a later qualification approval; partial and
ambiguous creation outcomes retain owned IDs for cleanup. Direct/gateway streaming, 32K,
auth/Host refusal, cancellation, comparison and production adoption remain separately gated.


## Stage A outcome

The [Stage A report](stage-a-report.md) records the approved installation, independent
receipt verification, paired deployment and successful schema16 migration. The original
MLX generation/selection/intents and gateway route are preserved. Both bounded existing-
route checks pass; Splash is disabled and has no owned serving processes. Code drift is
zero. Matched schema15/app/interpreter/env/LaunchAgent/host backups remain available, and
the temporary host-agent relay is closed. No rollback was required or claimed as live-
rehearsed. Later serving/gateway/context/cancellation/measurement work remains gated.


## Stage B preparation

The [functional qualification worksheet](stage-b-qualification.md) and
[request contract](qualification.json) define a separate proposed 45-minute window,
including ten minutes for restoration and at most 40 inference attempts. The ignored
Stage B packet resolves the current schema16 baseline, both installed receipts, exact
commands, private route/key bodies and cleanup. Offline near-limit inputs each render
to 31,873 prompt tokens with the installed tokenizer/template; no inference is implied.

Preparation and synthetic operating tests establish no live serving result. This window
needs its own explicit approval before temporary enable, lifecycle transitions or gateway
mutation. It preserves schema16 observations and restores the original MLX selection,
service states and disabled environment; normal cleanup does not restore schema15.
The frozen performance comparison and production decision remain separate.


## Stage B outcome and corrective packet

The [attempt report](stage-b-report.md) records the startup timeout, zero Splash
inference, successful owned cleanup and original MLX/services/disabled-env/gateway
restoration. Six availability observations remain separate from any new window.
The ignored `.private/splash-phase3/readiness-fix/REVIEW.md` contains the concrete
paired corrective apply, matched schema16 code/database backups, rollback and fresh
30-scheduled/40-cap functional window. No corrective apply or rerun has occurred.

The corrected source passes 3,344 tests (two skipped), 98.14% coverage, strict mypy
and Ruff. The shared precision helper has 100% statement/branch coverage. Eight
deterministic signal injections cover the cleanup race, and 34 private operating
checks cover budgets, routing identity, service drift, ownership and evidence scoping.
These are repository/preparation results, not a live qualification pass.
