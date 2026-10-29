# Splash Phase 0 protocol captures

Scrubbed functional captures from the approved isolated stock 27B 8-bit and modified
27B 4-bit smoke checks on 2026-10-25: MLX VLM 0.7.3 with MLX 0.32.2, and Splash 1.4.0.
Both used reasoning off, BF16 attention KV, FP32 recurrent state, and a 96-token stream cap.
The instruction prompt is from the public experiment corpus. Bare backend filenames are
the stock captures; `modified-4bit-` filenames are the separate derivative captures.

Model IDs, request IDs and creation timestamps are replaced with public-safe values.
`native-probes.json` contains allowlisted projections of actual health/readiness, model-list,
status/metrics and functional checks, plus byte SHA-256 references to private source captures.
It omits deployment/device/path metadata. Readiness is also checked with real inference;
health or a model-list response alone does not prove a loaded model.

These fixtures test parser/accounting compatibility. Their incidental timings do not
constitute a benchmark comparison or qualify the adoption gates. Raw private captures,
logs and separate timed measurements are retained outside the repository.
