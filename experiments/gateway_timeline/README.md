# Shared gateway timeline rollout workload

This bundle is for a separately approved live window. Run the same fixed workload before
and after deploying the timeline. Set `GATEWAY_BASE_URL` to the shared listener's `/v1`
base, plus `INFERENCE_API_KEY` and
`INFERENCE_MODEL_ID` in the private shell environment. Keep output JSON under an ignored
private directory such as `data/`; it contains request correlation IDs. Never commit it.

```bash
uv run python experiments/gateway_timeline/bench.py run --phase off --out data/timeline-off.json
# deploy the approved admin revision after backing up admin.sqlite
uv run python experiments/gateway_timeline/bench.py run --phase on --out data/timeline-on.json
uv run python experiments/gateway_timeline/bench.py compare data/timeline-off.json data/timeline-on.json
```

The run excludes two warmups, then sends 12 serial streams, six concurrent pairs and four
cancelled streams. It records client time to first data event, first nonempty content and
end, plus status and request ID. The comparison reports median and p95 for measured
completed samples, counts for failures and cancellations, and refuses a changed payload
or incomplete schedule. Check native idle state before and after each phase, and confirm
the on-phase IDs appear in the timeline detail API. The comparison is observational;
deployment, cache state and external load can change between phases. Preserve any failed
or partial run as such and do not claim a measured tracing overhead from these values.
