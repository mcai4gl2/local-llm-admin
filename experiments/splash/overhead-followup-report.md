# Modified-MLX collector overhead follow-up

**Decision: eligible.** This is the separately frozen four-window collector-overhead audit. Native cross-engine speed remains unassessed.

The prior expanded attempt stopped after two excluded warmups because the reviewer rejected the frozen multi-turn 2K input. Its inconclusive report and artifacts remain unchanged. This follow-up corrected that validator bug, validated all exact inputs before launch, and used fresh nonces, commands, receipts and output paths. No pilot or failed-attempt records are pooled.

## Accounting and bounds

Completed 4 independently restored ABBA windows: 144 requests, 48 excluded category warmups, 48 measured original/instrumented pairs and 72 fully accounted native traces (including instrumented warmups). Every request used a 256-token length cap, zero prompt reuse, temperature 0, seed 0 and reasoning off at a 32K configured context, with GPU sidecars stopped.

Instrumented/original client wall-time ratios below quantify collector overhead. Eligibility requires both median and upper 95% bound <=1.05 overall and in every category. Ratios below 1 do not establish that instrumentation accelerates inference.

| Category | Pairs | Median ratio | 95% interval | Decision |
| --- | ---: | ---: | --- | --- |
| Overall | 48 | 0.997389 | [0.990996, 0.999361] | eligible |
| short | 16 | 0.999246 | [0.997571, 1.000236] | eligible |
| 2k | 16 | 0.992225 | [0.988244, 1.000738] | eligible |
| 8k | 16 | 0.993867 | [0.978804, 1.002261] | eligible |

Intervals use the frozen 10,000-draw, seed-0 hierarchical bootstrap over four whole windows, paired launches, categories and paired repetitions. Four windows are the independent top-level units; requests within a window are dependent. The full fixed schedule was collected without early eligibility stopping or sample-count extensions.

## Restoration and provenance

Each window passed real original-service inference, admin loading, unchanged configuration/intent, target asset hashes and sidecar restoration. The experiment listener closed and owned experiment processes exited within the approved guard window. Stable resource/thermal observers ran through capture; one-off source/tokenizer audits ran outside timed phases.

| Window | Requests | Native traces | Minimum available GiB | New swap MiB | Restored |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 36 | 18 | 15.08 | 0.00 | yes |
| 2 | 36 | 18 | 14.86 | 0.00 | yes |
| 3 | 36 | 18 | 15.18 | 0.00 | yes |
| 4 | 36 | 18 | 14.83 | 0.00 | yes |

Final source audit: all isolated source inventories and bootstrap unchanged, 57 installed runtime files unchanged, and 41 source/import receipts reviewed across the earlier pilot, incomplete attempt and follow-up. Only this fresh four-window series contributes to the bounds.

The final original-service generation matched the real readiness proof, with no active admin jobs. Generation-checked cleanup closed the owned SSH master, temporary agent bridge and loopback tunnel; all thermal observers exited, temporary sockets/PATH entries were removed, and the original host agent was untouched.

See [machine-readable evidence](overhead-followup-evidence.json), [frozen follow-up design](mlx-overhead-followup-design.json), [incomplete attempt](expanded-overhead-report.md), [earlier native pilot](native-validation-report.md), and [32K context smoke](context-report.md). Private receipt digests allow provenance checks without publishing prompts, replies, token IDs or deployment facts.

## Limits and next work

This modified-checkpoint MLX audit evaluates collector overhead only. Its result creates no new stock-checkpoint measurements and does not remove the disclosed observer noise or timeout from earlier pilots. It does not establish isolated GPU time, native cross-engine speed, the broad 2-point quality margin, replay/concurrency, idle/restart, sidecar coexistence or authenticated gateway compatibility. A later native timing series needs its own frozen design and eligible collectors for its selected profiles.
