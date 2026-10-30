# Resident sharing follow-up

Status: Deferred; the bounded PaddleOCR rollout is complete.
Component: Platform / resident services

The [native rollout report](../../../docs/reports/ocr/20261031-native-rollout.md) and
[archived rollout plan](../../archive/platform/20261030_paddleocr-rollout-qualification.md)
preserve successful and failed outcomes. OCR is enabled for sharing with only the chat
model actually measured. Other model names, private settings and raw evidence stay outside git.

## Additional sharing models

Not qualified. Next decision: choose an additional model and context, freeze a separate
baseline and collect the same task, load, complete-memory and lifecycle evidence before
expanding the private OCR allow-list. Existing static compatibility estimates are not a
measured joint-load result. Keep the original corpus and failed outcomes intact.

## Long-running embedding allocator drift

Observed, not fixed by this rollout: a long-running embedding process held about 49 GiB
physical footprint before restart; the fresh runtime used about 5.85 GiB. The bounded
qualification and maximum-input tests passed with normal pressure, but do not establish
indefinite memory stability or validate a 9 GiB reservation for every embedding workload.

Next decision: define representative sustained embedding/reranking traffic, capture
allocator/cache and physical-footprint growth, and evaluate an explicit bound or safe
maintenance policy. Do not treat restarting before a measurement as proof of a leak fix.
Current automatic crash recovery remains off; preserve histories and unrelated runtimes.
