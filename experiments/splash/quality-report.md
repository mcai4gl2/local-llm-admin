# Splash deterministic quality pilot

The completed client timing series is unchanged. These new quality captures use a separate contract; native decode and the remaining adoption checks stay unassessed. No integration or deployment occurred.

The initial stock MLX pilot captured 15 of 35 cases and stopped on `ma-sum` at its 256-token cap. Fourteen prior replies and the truncated case are retained. No candidate quality request was sent in that attempt. An earlier controller metadata error sent zero quality cases; restoration passed after both attempts.

A separately frozen follow-up uses the same 35 questions and graders, with a 512-token completion cap for every case on both engines. Temperature 0, seed 0, reasoning off, concurrency 1, fresh cache namespaces, 60-second request deadlines and the previously verified exact asset/profile conditions apply. The stock follow-up kept the first attempt's original collection cutoff and restoration deadline. A separate bounded window covers the selected modified 4-bit checkpoint.

Suite SHA-256: `25a34b84cd86234b561820fcb26b46b8164bafddbe8c6ed0761569d5ef321fb9`. See [suite](quality-suite-512.json), [evidence](quality-evidence.json) and [decision rules](qualification.md).

| Checkpoint | MLX score | Splash score | Difference (points) | Approximate 95% interval (points) | Decision |
|---|---:|---:|---:|---:|---|
| stock-8bit | 88.57% | 88.57% | +0.00 | [-12.55, +12.55] | inconclusive |
| modified-4bit | 85.71% | 85.71% | +0.00 | [-12.55, +12.55] | inconclusive |

The fixed-suite margin remains a maximum two-point score loss, with required candidate tool cases passing individually. The conservative approximate paired interval uses cases as sampling units. Thirty-five cases cannot establish that margin from ties alone; an inconclusive outcome is not a quality pass. Repeated copies of these cases cannot supply independent evidence.

## stock-8bit

Capture status: mlx complete (35/35), splash complete (35/35).

Issues: none.

Candidate-only failures: none. Candidate-only passes: none.

Shared failures: if-five-words, ex-meeting, code-bool, code-closure.

| Category | Cases | MLX pass rate | Splash pass rate |
|---|---:|---:|---:|
| coding | 10 | 80.00% | 80.00% |
| extraction | 5 | 80.00% | 80.00% |
| instruction | 5 | 80.00% | 80.00% |
| math | 5 | 100.00% | 100.00% |
| reasoning | 5 | 100.00% | 100.00% |
| tools | 5 | 100.00% | 100.00% |

## modified-4bit

Capture status: mlx complete (35/35), splash complete (35/35).

Issues: none.

Candidate-only failures: none. Candidate-only passes: none.

Shared failures: if-five-words, ex-meeting, re-weekday, code-bool, code-closure.

| Category | Cases | MLX pass rate | Splash pass rate |
|---|---:|---:|---:|
| coding | 10 | 80.00% | 80.00% |
| extraction | 5 | 80.00% | 80.00% |
| instruction | 5 | 80.00% | 80.00% |
| math | 5 | 100.00% | 100.00% |
| reasoning | 5 | 80.00% | 80.00% |
| tools | 5 | 100.00% | 100.00% |

## Operations and limits

All attempts restored the original engine/admin with real inference and verified sidecars, intent/configuration, target hashes and experiment cleanup. Separate raw proof hashes and timestamps are in the evidence. The controller metadata error and original truncated pilot remain disclosed. Raw answers, prompts, manifests, paths and credentials stay private.

| Attempt | Minimum available GiB | Maximum new swap bytes | Restoration within bound |
|---|---:|---:|---|
| quality-stock-1 | 14.79 | 0 | yes |
| quality-stock-1-retry | 15.69 | 0 | yes |
| quality-stock-512 | 14.28 | 0 | yes |
| quality-modified-512 | 16.17 | 0 | yes |

This is a small deterministic pilot, not coding-synthesis, open-ended, multimodal or daily-workload qualification. Native timing/overhead, context/cache/concurrency/reasoning/operations campaigns and actual gateway inference remain separate requirements. A failed gate stops integration under the parent plan; incomplete or uncertain evidence cannot imply adoption.
