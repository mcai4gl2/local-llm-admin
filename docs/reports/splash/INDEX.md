# Splash rollout evidence

Historical, sanitized records of bounded deployment and qualification windows. Start with
the current [operating guide](../../../host/splash/README.md) and
[shared LiteLLM endpoint](../../shared-inference.md) for present configuration.

| Record | Meaning |
|---|---|
| [Rollout](rollout-report.md) | Delivered functional support and dedicated native endpoint qualification |
| [128K](context-128k-report.md) | Both profile context checks and their token/latency limits |
| [Integrated comparison](integrated-comparison-report.md) | Partial/inconclusive performance evidence; no preferred-backend adoption |
| [Stage A](stage-a-report.md) | Installation, receipts and database migration |
| [Stage B](stage-b-report.md) | Initial failed functional attempt and restoration |
| [Readiness correction](readiness-fix-report.md) | Preserved corrective attempt and outcome |
| [Gateway contract](gateway-contract-report.md) | Endpoint identity qualification |
| [macOS exit](macos-exit-fix.md) | Shutdown correction and verification scope |
| [Original runbook](README.md) | Historical proposals, budgets, execution order and revision-specific rollback |

Companion JSON files retain original results, budgets and source labels byte-for-byte.
These records are not fresh deployment approval and do not qualify code written later.
Earlier benchmark scripts/reports are in [experiments/splash](../../../experiments/splash/README.md).
