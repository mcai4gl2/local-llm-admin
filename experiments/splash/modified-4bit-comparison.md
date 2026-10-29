# Splash Phase 0 comparison

Decision: **inconclusive**

This report assesses measurement gates only. Full adoption requires the separate evidence below.

Rate metric: `client_output_wall_tok_s`.
Client output/wall rate includes prefill and HTTP overhead; it is not native decode throughput.

| Gate | Result |
|---|---|
| Decode speed | unassessed |
| p95 TTFT | pass |
| Fixed-length end-to-end | pass |

Median paired ratio: 2.215103762082901
95% paired session/prompt bootstrap interval: [1.9415804414981233, 2.7757297165213273]

Sample accounting: `{"early_stops": 0, "errors": 0, "measured": 90, "warmup": 18}`

| Category | Median paired ratio |
|---|---|
| short | 2.2151 |
| 2k | 2.7757 |
| 8k | 1.9416 |

Latency and actual output lengths:

```json
{
  "mlx": {
    "median_output_tokens": 1024,
    "median_total_s": 61.64100428018719,
    "p95_ttft_s": 21.61712969932705
  },
  "splash": {
    "median_output_tokens": 1024,
    "median_total_s": 22.11406950186938,
    "p95_ttft_s": 14.819566269032658
  }
}
```

Unresolved measurement issues:

- `client_wall_rate_is_not_native_decode`

Required adoption evidence:

- Quality and compatibility: pending
- Resources and service coexistence: pending
- Context, ownership, start/stop cycles and restoration: pending

Raw sample references (private artifact IDs and canonical SHA-256):

- `modified-4bit-mlx-1`: `c0fbb98d322e24e3df0dd471a5b0396ee4b2359c655cdd4a8689367d719fc4fe`
- `modified-4bit-mlx-2`: `db6b0ce51795889d20db01c0d32b0210ef505f5167f90543b3c3a7c430cbc10b`
- `modified-4bit-mlx-3`: `4fd35ad931415cda2f51ba82e6129385cf9495ac09e8102b3a512fdbb3942fb4`
- `modified-4bit-splash-1`: `e0db338a340235d54f7853d57b91b78e2317a6179a0234c8b511473925ce9e2d`
- `modified-4bit-splash-2`: `bd620ceea116bed9a3ad488cc92e4667e56efceb56ab692f9f84bb80e9c4c15a`
- `modified-4bit-splash-3`: `090945a3454e72dba87e01769ffbacf12e29e81219e1665c3a448733da83b9d7`
