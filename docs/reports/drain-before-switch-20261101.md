# Drain-before-switch rollout and bounded qualification

Date: 2026-11-01
Scope: shared gateway admission and admin lifecycle control on the existing Mac deployment.
Outcome: admin deployed; completed stream-before-switch, cancellation, drain timeout and
failed target-readiness rollback qualified live.

## Local validation

- Final full gate before deployment: 3,863 passed, 4 skipped, 97.19% total coverage.
  Admission and inference gateway each had 100% statement and branch coverage.
- Ruff, strict mypy and the private-pattern public leak scan passed.
- A subsequent focused test passed for an admitted stream finishing before the barrier
  allows transition; it changed tests only, not deployed source.
- The final full gate after live qualification and plan archiving passed: 3,864 tests
  passed, 4 skipped, 97.20% coverage. Ruff and strict mypy passed again.

## Deployment and live checks

- Before deployment, the admin was healthy with no running jobs; MLX native metrics showed
  zero in-flight requests and zero queue depth.
- The approved admin deployment completed and health returned. Installed changed runtime
  modules and Models assets matched the tested worktree byte for byte.
- The deployed Models page included drain progress. Authenticated `/v1/models` and a
  bounded non-streaming completion succeeded; the selected engine stayed MLX.
- A controlled long stream held one gateway lease. Cancelling a pending stop returned a
  refusal before any stop job; admission reopened and MLX remained active. Native metrics
  later returned to zero in-flight work. The gateway recorded unverified native cleanup
  for cancelled transports, which is not proof of all-client native quiescence.
- With a temporary five-second private drain deadline, a second long stream held one
  lease until the pending stop timed out at about 5.4 seconds. No stop job began. Native
  metrics returned to idle. The original private environment was restored, the admin
  restarted, and gateway completion and native health passed again.
- When pressure returned to normal, a shared-gateway stream completed 1,002 data chunks
  over about ten seconds. A same-model switch waited with one active lease and no job;
  after the stream completed, the switch job succeeded. MLX remained the active engine
  and admission returned to `accepting`.
- With approval to interrupt and restore the two idle residents, a temporary private
  ComfyUI readiness URL pointed at an unreachable loopback port with a three-second
  readiness deadline. The engine job timed out and reported `rolled_back: true` with
  MLX observed. Both residents were running and idle again, the gate was `accepting`,
  and no job remained. The original private environment was restored byte for byte and
  the admin restarted healthy. A subsequent gateway completion returned 200; native
  MLX metrics showed zero in-flight requests and zero queue depth.

## Qualification history and limits

A first same-model switch was refused before drain reservation because live memory pressure
was `warn` while two resident services were running. No switch job began. The switch was
qualified later when pressure returned to normal. The resource guard was respected. The
exclusive target stopped the residents only after approval, and its rollback restored them.

The read-only host drift check also found pre-existing missing OCR host files; this rollout
deployed admin code only and did not apply the host manifest.

Gateway counts cover the shared `/v1` listener. Admin chat, resident APIs and direct native
callers are outside this registry. The cancellation counter and later native idle sample
cannot correlate every cancelled gateway request to native work on MLX, Strata or Splash.
