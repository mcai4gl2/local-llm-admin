# Integrated comparison — bounded partial results

Both frozen windows are closed and original service restoration is verified. Overall
performance/adoption remains **inconclusive**. The design and earlier Phase 0 results
remain unchanged; no window, failed setup or budget was reset. These captures precede
the dedicated-endpoint isolation change.

| Profile | Successful HTTP attempts / cap | Stop condition | Unassessed |
|---|---:|---|---|
| Stock | 24 / 36 | Restoration-reserve guard | Two remaining 2K admin-off samples, eight gateway samples, two context checks |
| Modified | 26 / 36 | Temporary gateway key creation returned HTTP 400 | Eight gateway samples and two context checks |

All attempted inference/original-route requests passed. Management provisioning calls
are separate; the rejected key was independently absent before deleting the two owned
temporary routes. The operator generated a key without the expected prefix; this matches
[upstream key validation](https://github.com/BerriAI/litellm/blob/main/litellm/proxy/management_endpoints/key_management_endpoints.py).
The creation error body was not retained, so its exact server-side reason remains unverified.
The generator is corrected in a new operator packet; the failed window stays unchanged.

## Same-case diagnostic medians

One excluded warmup and three measured repeats per completed group; concurrency one,
temperature/seed zero, reasoning on, identical prompt counts within each paired repetition.
Splash BF16 KV/FP32 state was verified. Temporary MLX profiles used a 32K unquantized-KV
launch with thinking enabled; complete launch arguments and runtime receipts stay private.
Output caps were 512/1024. Actual lengths, early stops/truncation and missing first-answer
values remain in the structured report. Rates from final runtime responses are distinct
from instrumented native-emission rates; no comparison with Phase 0 callback boundaries
is inferred. Different outputs/lengths and fixed condition order limit wall-time claims.

| Profile/case | MLX wall (s) | Splash admin-on wall (s) | Admin-on/off wall ratio | Admin-on/off TTFT ratio |
|---|---:|---:|---:|---:|
| stock / instruction-short | 52.175 | 22.205 | 0.9999 | 1.1044 |
| stock / code-2k | 108.973 | 40.983 | unassessed | unassessed |
| modified / instruction-short | 28.849 | 12.849 | 1.0077 | 0.9620 |
| modified / code-2k | 61.902 | 14.785 | 1.0059 | 0.9967 |

## Material-overhead review

Stock short TTFT is 0.740 s with admin versus 0.670 s without: +10.4%, about 70 ms,
above the 5% investigation threshold. Three-sample ranges overlap (on 0.599–0.750 s;
off 0.602–0.864 s), while wall medians are effectively equal. The same complete native
generation/instance/fingerprint survived admin stop/resume, restart count stayed zero,
reuse stayed zero, and observed thermal/pressure state stayed nominal/normal. The on
condition also performs extra admin guard calls, with fixed order and no causal isolation.
The finding remains inconclusive; no timing is corrected and no ingestion-only bound is
claimed. Both completed modified cases stay within the 5% monitor TTFT/wall review threshold.

## Provenance, restoration and limits

Weights/tokenizer/template groups match the frozen cohorts. Original MLX library bytes
(1,477 files), four package versions and 457 framework RECORD files match the archived
source evidence. Of 57 original runtime-file receipts, 56 match; the sole expected change
is the reviewed host control wrapper. The archived MLX aggregate marker is retained and
was not re-derived. All 43 Splash runtime files match. This is an explicit provenance
limit, separate from source-byte identity. No runtime/model installation occurred.

Both windows restored original MLX, actual services/sidecars, semantic intents, disabled
Splash, original authenticated gateway inference and unchanged original routing. Temporary
MLX profile absence and its previous-file hash were restored, with the known temporary
profile preserved on its own volume. Schema 16 integrity is `ok`; snapshots/cursors were
retained and no SQLite main-file restoration occurred. Native members, receipts, jobs
and locks are clear. Final pre-isolation totals: 66 native readings / 94 availability rows.

The [structured results](integrated-comparison-results.json) include private raw-artifact
references and hashes, individual timing distributions, unknowns and every unattempted
slot. No raw prompt, output, credential, process ID or deployment address is published.
Gateway/context performance in these windows is unassessed. Earlier independent functional
[HTTP qualification](gateway-contract-report.md), [normal cycles](macos-exit-fix.md),
[engine matrix and native/admin acceptance](integration-acceptance.md) retain their scopes.
The final enabled deployment requires separate dedicated-endpoint qualification.
