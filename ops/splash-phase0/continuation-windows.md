# Approved continuation windows

Status: **both approved continuation windows complete and independently restored**.
The first approved window is complete.
This worksheet covers only the remaining primary timing sessions for the user's selected
stock Qwen3.8-27B 8-bit and modified Qwen3.8-27B 4-bit checkpoints.
The user separately approved these two bounded maintenance windows on 2026-10-25.

## Concrete scope

Use the same pinned runtime/draft, target/tokenizer/template fingerprints, public corpus,
precision, generation settings, context/memory budgets, resource thresholds and verified
stop/start/restoration procedure as [the first window](benchmark-window.md).
Reuse the isolated installation and prepared assets. Preserve the frozen measurement contract;
the [native timing audit](native-timing-audit.md) explains why native decode stays unqualified.

Approved: **two separate windows, each capped at 120 minutes**, with original-service restoration
and verification between them. Each window contains one paired session for each checkpoint:

| Window | Session | Stock 8-bit order | modified 4-bit order |
|---|---:|---|---|
| 1 | 2 | Splash → MLX | Splash → MLX |
| 2 | 3 | MLX → Splash | MLX → Splash |

Each backend run has three tasks, one excluded warmup per task and five measured repetitions:
18 requests per run, 72 per window. Together these add 120 measured requests and 24 excluded
warmups to the existing 60 measured requests and 12 warmups. At completion, each backend/checkpoint
has 45 measured TTFT samples in the frozen task mix; that permits the planned pooled p95
calculation if all evidence remains valid. It does not establish a per-task p95 or an adoption pass.

## Duration estimate from saved evidence

The offline [planner](continuation.py) uses the slowest observed request per case/backend,
including warmups, multiplies by six requests per case, and adds a 25% slowdown margin.
It packs whole paired sessions and keeps checkpoint results separate.

| Checkpoint / backend | Budgeted request minutes per window |
|---|---:|
| Stock 8-bit / MLX | 36.19 |
| Stock 8-bit / Splash | 14.70 |
| modified 4-bit / MLX | 21.50 |
| modified 4-bit / Splash | 9.81 |
| Request subtotal | 82.20 |
| Setup, probes and engine switching reserve | 15.00 |
| Restoration and verification reserve | 10.00 |
| Estimated total | **107.20** |
| Maximum authorized duration | **120.00** |

These are planning estimates, not a promise of completion. An independent deadline supervisor
must stop collection no later than 110 minutes after activation and restore within 120 minutes.
Retain partial evidence if slower execution or another abort condition consumes the bound.
Do not extend a window or reduce the restoration reserve to finish samples.

Reproduce the private worksheet inside the devcontainer:

```bash
uv run python ops/splash-phase0/continuation.py \
  --cohort stock-8bit /tmp/splash-phase0/results/stock-8bit \
  --cohort modified-4bit /tmp/splash-phase0/results/modified-4bit \
  --window-minutes 120 --setup-minutes 15 --restore-minutes 10 --slowdown 1.25 \
  --out /tmp/splash-phase0/continuation-plan.json
```

The planner refuses overwrite; choose a new private output filename when regenerating.
It validates all supplied evidence, including failures, warmups, matching paired prompts,
frozen conditions and contiguous alternating sessions. It never contacts a server, starts a
process or grants approval. Its source digests identify the input run artifacts; it does
not turn old preflight observations into current execution evidence.

## Checks before each approved activation

Refresh and privately review the original serving process generations, selected model/pack,
service/sidecar intent and observed state, admin loaded state, configuration hashes, listener
ownership, active jobs, assets and free memory/disk. The current read-only inventory has no
active admin jobs, unchanged semantic restore state and no experiment listener/processes,
but remains an inventory requiring identity/readiness review and fresh asset verification.

Prepare a new private guard configuration with the actual activation/deadline times and
fresh original process generation. Archive the completed guard's mailbox/state/logs/proofs;
preserve the first-window evidence. The old `rearm.sh` deadline has expired and must not be
reused. Verify the new daemon identity before issuing activation, and freeze reviewed
session-2/3 manifests with fresh preflight references before their first requests.

Stop the current engine/admin only through the reviewed recipe after live approval.
Recheck identity and real readiness on every launch. Abort on the original resource,
ownership, configuration/intent, readiness, stream/accounting and deadline conditions.
Always restore and independently verify real original inference, admin/sidecar state,
experiment cleanup and unchanged configuration; report gateway inference as unverified
unless an appropriately scoped existing route credential actually proves it.

This scope excludes new runtimes, extra model copies, production integration/deployment,
task-quality/context/concurrency/service-mix campaigns and changes to the default engine.
Those remaining adoption gates stay visible in the report.

Session 2 supervisor armed at 09:01:46 UTC on 2026-10-25; collection cutoff 10:51:46 UTC,
restoration deadline 11:01:46 UTC. Its frozen manifests retain the session-1 conditions and
new reviewed preflight references. No deployment is included in this approval.

Session 2 completed all 60 measured requests and 12 excluded warmups without benchmark errors
or early stops. Original service restoration and real inference passed within the approved
bound. Additional read-only sidecar/snapshot reviews passed before session 3. Session 3 supervisor armed at 11:37:00 UTC on
2026-10-25; collection cutoff 13:27:00 UTC, restoration deadline 13:37:00 UTC. Its order is
MLX then Splash for each checkpoint, with unchanged frozen conditions.

## Completed continuation

Both windows completed their four frozen runs: 120 measured requests and 24 excluded
warmups, with zero benchmark errors or early stops. Together with session 1, each checkpoint
has three paired sessions and 45 measured TTFT samples per backend. Original service
restoration and real inference passed within both bounds. Additional configuration, sidecar
and asset audits passed; session 2's expanded read-only review completed at 11:36 UTC, after
its restoration deadline and before session 3. Exact audit times are in evidence.json.

Session 2 restoration completed at 2026-10-25T10:10:19.895538+00:00 before its 2026-10-25T11:01:46.706785+00:00 deadline.

Session 3 restoration completed at 2026-10-25T12:43:55.612916+00:00 before its 2026-10-25T13:37:00.002142+00:00 deadline.

The [final report](report.md) assesses pooled client latency and fixed-length completion.
Native decode, task quality, context, concurrency, service coexistence and other remaining
adoption gates stay unqualified. These completed windows do not authorize deployment.
