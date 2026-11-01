# Shared request timeline live rollout

Date: 2026-11-01
Status: Completed; approved live deployment and bounded qualification passed.
Component: Platform / inference observability
Next decision: No further rollout step; retain the private pre-upgrade snapshot for
the documented offline schema rollback.

The [local implementation](20261101_shared-request-timeline.md) passed repository and
browser gates. The original devcontainer host-diff probe could not authenticate and
established no host drift result. A later host-SSH read-only check found all versioned
code files matching; the model registry differed as expected state. A local schema
drill verified backup, migration and offline restore before any live change.

In the approved window, native idle and schema 17 were checked, then the fixed
[`experiments/gateway_timeline`](../../../experiments/gateway_timeline/README.md) off
phase completed. A private, verified `admin.sqlite` snapshot preceded
`ops/deploy-mac.sh`. Migration 18, installed bytes, gateway inference, timeline API/UI,
correlation IDs, concurrent/cancelled streams, filters and writer health passed. The
matching on phase completed and the private raw samples were retained. The sanitized
[rollout report](../../../docs/reports/shared-request-timeline-20261101.md) records
results, initial verification-script errors and measurement limits. Keep the matching
pre-upgrade snapshot for the offline schema rollback described in
[shared inference operations](../../../docs/shared-inference.md).
