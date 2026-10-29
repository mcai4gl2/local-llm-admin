# Experiments

These tools are retained for reproducibility and reviewing measurements. They are not
imported by the running admin or deployed by the host manifest. Routine deployment and
maintenance remain in [ops](../ops/README.md).

| Bundle | Scope |
|---|---|
| [Splash](splash/README.md) | Phase 0 benchmark, qualification harnesses, designs and sanitized evidence |
| [Strata](strata/README.md) | Original engine benchmark and sandbox capture tools |
| [Embeddings](embed/reference.py) | Original embedding/rerank reference capture |
| [Decision](decision/README.md) | Original decision-sidecar qualification tools |

Old reports retain their pass/fail/inconclusive outcomes and original measurement scope.
Moving a bundle does not authorize new live experiments. Any tool that changes a running
service or launches model inference needs its own reviewed scope and explicit approval.
Raw target details, credentials and receipts stay private. Historical JSON source-path
labels describe the original capture location; their frozen bytes have not been rewritten.
The corresponding bundles moved from `ops/*-phase0/` to this directory.
