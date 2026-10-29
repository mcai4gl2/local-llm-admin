# Splash Phase 0B — three paired sessions per checkpoint

Decision: **inconclusive for adoption**. Stock 27B 8-bit and modified 27B 4-bit each completed the frozen three-session primary timing matrix. Client wall performance can now be assessed across counterbalanced sessions; native decode and the remaining adoption gates stay unqualified.

All three maintenance windows restored the original serving engine/admin with real inference, process identity, unchanged configuration/intent, sidecar-state review and experiment cleanup. No backend integration, deployment, model/profile registration or default change was made. Gateway inference verification remains limited below.

## Frozen conditions

MLX VLM 0.7.3 / MLX 0.32.2 versus Splash 1.4.0 and the pinned Qwen3.8-27B DFlash2 draft. Exact assets/runtime/draft fingerprints and raw-run digests are in [evidence.json](evidence.json). Each checkpoint uses the same weights/tokenizer/template on both engines; the two checkpoints are separate comparisons, not an 8-bit-versus-4-bit quality test.

Temperature 0, seed 0, reasoning off, concurrency 1, BF16 attention KV, FP32 recurrent state, 40 GiB Metal allocation budget and an effective 32,768-token request limit. The Metal budget is not a whole-process cap. The installed MLX source and runtime metrics confirm that its configured limit enforces input plus requested output. Actual timed inputs remain short, approximately 2K and approximately 8K text; production long context is untested. Fresh deterministic nonce prefixes and audited cache namespaces report zero reused tokens.

Session order for each checkpoint: MLX→Splash, Splash→MLX, MLX→Splash. Each run has one excluded warmup per task and five measured repetitions per task: 45 measured requests per backend/checkpoint, 180 measured requests and 36 excluded warmups overall. All outputs reach their 512/1,024-token caps, with zero benchmark request errors, zero early stops and matching paired prompt counts.

## Client performance

Wall throughput is validated completion tokens divided by complete client request wall time, including prefill and transport. Backend rates below pool 15 measured requests per task; brackets show observed minimum–maximum. The paired ratio takes the median of five paired request ratios in each session, then the median of those three session values. Warmups are excluded.

The overall point estimate and bootstrap interval in each strict comparison use the existing comparator: the median of the nine task/session paired medians, with 2,000 deterministic resamples of paired sessions and task families. Three sessions and three unique task families remain a small experiment; these bounds do not establish general workload quality or long-term behavior.

### Stock 27B 8-bit

Overall paired client wall ratio: **2.49×**, bootstrap 95% interval **2.20–3.09×**. This is not a native decode qualification.

| Task | Input tokens | Output cap | MLX wall tok/s | Splash wall tok/s | Paired wall ratio |
|---|---:|---:|---:|---:|---:|
| short | 105–111 | 512 | 9.91 [9.90–9.92] | 24.67 [22.72–26.21] | 2.49× |
| 2k | 1775–1779 | 1024 | 9.47 [9.46–9.48] | 29.77 [28.18–30.92] | 3.09× |
| 8k | 8147–8152 | 1024 | 7.98 [7.97–7.98] | 17.45 [17.04–18.27] | 2.20× |

| Task | MLX median TTFT (s) | Splash median TTFT (s) | MLX median completion (s) | Splash median completion (s) |
|---|---:|---:|---:|---:|
| short | 0.613 | 0.372 | 51.65 | 20.75 |
| 2k | 5.158 | 3.392 | 108.12 | 34.40 |
| 8k | 23.309 | 15.383 | 128.37 | 58.70 |

Pooled TTFT p95 across the frozen task mix (45 measurements/backend): MLX **23.404 s**, Splash **15.439 s**; gate **pass**. Fixed-length per-task/session median completion gate: **pass**. This is not a per-task p95 claim.

Server-reported native rates remain separate diagnostics with different timing endpoints; see the [pinned audit](native-timing-audit.md).

| Task | MLX native tok/s | Splash native tok/s |
|---|---:|---:|
| short | 10.01 | 25.12 |
| 2k | 9.94 | 32.94 |
| 8k | 9.74 | 23.64 |

Strict [comparison and raw-run references](stock-8bit-comparison.md).

### modified 27B 4-bit

Overall paired client wall ratio: **2.22×**, bootstrap 95% interval **1.94–2.78×**. This is not a native decode qualification.

| Task | Input tokens | Output cap | MLX wall tok/s | Splash wall tok/s | Paired wall ratio |
|---|---:|---:|---:|---:|---:|
| short | 105–111 | 512 | 17.91 [17.70–17.93] | 39.61 [37.62–41.84] | 2.22× |
| 2k | 1775–1779 | 1024 | 16.61 [16.56–16.63] | 46.31 [42.21–48.28] | 2.78× |
| 8k | 8147–8152 | 1024 | 12.71 [12.68–12.72] | 24.68 [24.22–25.47] | 1.94× |

| Task | MLX median TTFT (s) | Splash median TTFT (s) | MLX median completion (s) | Splash median completion (s) |
|---|---:|---:|---:|---:|
| short | 0.500 | 0.357 | 28.58 | 12.93 |
| 2k | 4.680 | 3.288 | 61.64 | 22.11 |
| 8k | 21.521 | 14.662 | 80.59 | 41.50 |

Pooled TTFT p95 across the frozen task mix (45 measurements/backend): MLX **21.617 s**, Splash **14.820 s**; gate **pass**. Fixed-length per-task/session median completion gate: **pass**. This is not a per-task p95 claim.

Server-reported native rates remain separate diagnostics with different timing endpoints; see the [pinned audit](native-timing-audit.md).

| Task | MLX native tok/s | Splash native tok/s |
|---|---:|---:|
| short | 18.19 | 41.01 |
| 2k | 17.95 | 54.49 |
| 8k | 17.31 | 38.22 |

Strict [comparison and raw-run references](modified-4bit-comparison.md).

## Compatibility, resources and operations

Session 1 smoke checks passed READY, constrained JSON, the forced echo tool call, reasoning-on arithmetic with the correct answer, and cancellation/slot release on all four target/runtime combinations. Later launches repeat readiness, model identity and effective-profile checks. Those checks do not replace task-quality scoring, vision/PDF testing or a cold/idle/restart campaign. Intentional first-session cancellation probes are excluded from benchmark error counts.

| Checkpoint/runtime | Minimum available memory (GiB) | Maximum new swap growth during a run (MiB) |
|---|---:|---:|
| stock-8bit / mlx | 23.75 | 0.00 |
| stock-8bit / splash | 10.70 | 0.00 |
| modified-4bit / mlx | 28.39 | 0.00 |
| modified-4bit / splash | 17.65 | 0.00 |

Resource bounds cover primary runs including warmups. Per-session observations, OS process-tree RSS and native allocation peaks remain separate in evidence.json; accounting differs between runtimes and neither process RSS nor the Metal budget is total host memory. Available memory/pressure/swap were monitored by the independent host supervisor. Intended service-mix coexistence remains untested. Direct temperature readings were unavailable; do not infer an absence of thermal bias solely from counterbalancing or nominal warning checks.

The first window had two native startup failures caused by empty optional authentication and macOS Bash 3 nounset handling in the harness. Both were cleaned up and fixed before timed requests; they remain operational failures, not benchmark samples or successful restart cycles. Session-3 preparation also stopped on a missing helper argument before activation; its invocation was corrected without replacing captures or restarting timed runs. Later primary runs do not establish idle-release behavior.

## Restoration and verification scope

Each window independently verified original serving identity/model/pack, real inference, original admin health, unchanged semantic intent/configuration/selection, prior sidecar observed states, closed experiment processes/port and unchanged benchmark target weight/tokenizer/template fingerprints. Original service restoration and real inference completed within each approved two-hour bound. Additional read-only state reviews passed before the next activation. In session 2, original inference was verified at 10:10 UTC before the 11:01 UTC deadline; the expanded sidecar/snapshot review was completed at 11:36 UTC, after that deadline and before session 3. Evidence records these audit timestamps separately from the restoration proof. See the per-window proof digests in evidence.json.

Gateway liveness/readiness and authenticated model-list/model-info endpoints returned HTTP 200 in every restoration check. The existing read-only key did not establish an inference route; authenticated end-to-end gateway inference remains unverified. The admin gateway configuration hash is unchanged.

## Remaining decision gates

The three-session primary client timing matrix is complete. The native decode gate remains unassessed: MLX and Splash use different timing endpoints, and MLX reconstructs predicted duration from its rate. The frozen client wall metric cannot qualify native decode. No statistic or measurement contract was retrofitted to claim a pass.

Task-quality scoring/uncertainty, fresh approximately 32K and replay workloads, concurrency 2/4, required production context/retrieval, image input if required, deliberate cold/idle/restart distributions, intended service coexistence and authenticated gateway inference remain pending. Full backend integration and deployment stay gated.

Raw manifests, measurements, streams, logs, inventories, resource histories and restoration proofs stay private outside git. Only scrubbed fixtures, aggregates and content digests are published.
