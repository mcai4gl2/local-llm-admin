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

Median paired ratio: 2.488499187014753
95% paired session/prompt bootstrap interval: [2.1967706959195765, 3.088767780977436]

Sample accounting: `{"early_stops": 0, "errors": 0, "measured": 90, "warmup": 18}`

| Category | Median paired ratio |
|---|---|
| short | 2.4885 |
| 2k | 3.0888 |
| 8k | 2.1968 |

Latency and actual output lengths:

```json
{
  "mlx": {
    "median_output_tokens": 1024,
    "median_total_s": 108.12045371346176,
    "p95_ttft_s": 23.404468187317253
  },
  "splash": {
    "median_output_tokens": 1024,
    "median_total_s": 34.402563146315515,
    "p95_ttft_s": 15.439457314647735
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

- `stock-8bit-mlx-1`: `97eb0527af65a5c26b6673b7f9c5ac8e4b89c19f3d4833c8e4d837b980b64365`
- `stock-8bit-mlx-2`: `900600b62804af9a5df605128b13ccb1a579686a8fc47a7864dc5f662b3461fd`
- `stock-8bit-mlx-3`: `141be5b4fec5e281f61d946cdf01a986bc5369a04cca34f0b03f1f7afd476daf`
- `stock-8bit-splash-1`: `af3f2184cdc549f6be4ecf35e09c98c50640c08bd707c4ea18bd13c8e7e213d2`
- `stock-8bit-splash-2`: `f1cbb0337498eb6bde762d977ad7d2fe257aa72ab45117a8e2430d1b4273ebc1`
- `stock-8bit-splash-3`: `bbcb1049a7919c210fa90c0352c2b64f87b2d522ef9e2f0f594d9fb4a868bb24`
