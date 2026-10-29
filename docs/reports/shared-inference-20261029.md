# Shared inference incident and deployment verification

## Confirmed failure sequence

The Mac was still running the deployment from before the shared inference listener was
merged. Its private native-port topology was absent. The latest source in git had not
been deployed automatically by the merge.

- Strata completed requests. Several five-token requests exhausted their entire output
  limit during reasoning and returned no final answer. Other requests spent minutes
  reasoning. The inspected log does not demonstrate a recent Strata crash.
- Splash reached ready state on its separate native port while the existing LiteLLM
  route still addressed port 8080. LiteLLM recorded connection failures during this
  interval. The engine switch itself completed successfully.
- MLX then loaded the selected local model successfully. A later request with model
  `x` caused native MLX to unload that model and attempt an offline Hub lookup for `x`.
  The lookup failed, leaving an HTTP-healthy process with no loaded model. Correct-ID
  inference restored MLX, but foreign-model requests subsequently displaced it again.

LiteLLM's inspected route uses `/Volumes/Models/models/active`. The admin chat builds
requests from the selected backend descriptor. The source of the foreign-model requests
was not established; neither client is attributed as their source by this report.

## Repair applied

Deployed the paired shared-listener app and host changes from merged revision
`68342fc3909008643703c9a923a8ca01ec8fcc03`. The private frontend key matches the key already
configured on the existing LiteLLM route. Its public URL, upstream model ID and key were
preserved. The internal topology follows the [operating guide](../shared-inference.md):
MLX on loopback port 8086, Strata on loopback port 8087 and Splash on loopback port 8085,
behind the shared listener on port 8080.

A private revision-pinned packet includes payload hashes, original source/configuration
backups, a consistent SQLite snapshot, command receipts and a matched rollback procedure.
The preflight checked idle jobs, free ports and unchanged dependencies. The admin/watchdog
was stopped before changing the native owner, and the maintenance gate protected the
paired source/configuration installation. No dependency, model-weight or native-runtime
upgrade was required. The original selected MLX model and sidecar intent were restored.

Unknown model IDs now receive 404 before native dispatch. They cannot ask MLX to unload
its selected weights through the shared entry point. The native MLX listener was verified
on loopback, preventing LAN clients from bypassing that model check.

## Verification

All 127 focused shared inference, startup, native-port and host-control regression tests
passed before deployment. The previously passing full PR CI remains scoped to its exact
merged source; this incident branch changes documentation only.

| Engine/configuration | Same URL and stable model ID | Non-streaming answer | Streaming answer | Admin test chat | Foreign-model refusal |
|---|---|---|---|---|---|
| Strata IQ3_XXS | Pass | Pass | Pass | Pass | Pass |
| Splash modified 4-bit profile | Pass | Pass | Pass | Pass | Pass |
| Splash stock 8-bit profile | Pass | Pass | Pass | Pass | Pass |
| Original selected MLX model | Pass | Pass | Pass | Pass | Pass |

Additional checks passed:

- A request through the actual LiteLLM proxy route returned HTTP 200 with an answer.
- Stable model IDs were retained in streaming and non-streaming responses.
- Invalid frontend credentials received 401; inference credentials did not grant admin
  API access. Authenticated admin chat completed normally.
- Rejecting model `x` left MLX's loaded model unchanged.
- Disconnecting a live stream allowed the next request to answer, demonstrating recovery
  of admission and the native decode slot.
- Installed app and paired host payload hashes matched the pinned merged revision.
- Original selected weights, engine/sidecar intent and idle-job state were verified after
  switching through all configurations.

An initial private verification harness rejected an upstream `content: null` stream frame;
the harness was corrected to handle that valid frame, and verification then passed. No
production change was needed for it. Historical qualification JSON was not rewritten.
These are bounded functional checks, not new long-context or performance qualification.
For a short smoke test, disable thinking or allow enough output tokens for reasoning and
an answer; a tiny token cap is not proof that the engine failed to load.


## Follow-up: selected Strata request failures

After the user selected Strata again, the switch job succeeded and native health showed
the model loaded. A request through the actual LiteLLM proxy, with a finite output limit
and normal thinking enabled, returned HTTP 200 with a nonempty answer. This does not
reproduce every client request.

Additional evidence established two distinct failure modes:

- A request with a near-context-sized output allowance spent five minutes reasoning in
  the single decode slot, then cancelled at the frontend timeout. Another request of
  the same prompt length began. A diagnostic admin chat queued behind it timed out.
  LiteLLM recorded `Shared inference is busy` rate-limit errors and cooldown/retry
  messages. Request origin and which client owned the repeated work remain unproven.
- The native server rejected image input because its vision encoder is not installed.
  A minimal image request reproduced the HTTP 400. The deployed shared frontend hid
  that specific reason behind its generic refusal message.

The incident branch now includes a bounded translation of the recognized vision refusal
to a fixed public-safe explanation. It retains the original status and error code and
keeps arbitrary upstream text, paths and credentials private. The regression first failed
against the generic refusal; all 55 focused inference tests pass with 100% statement and
branch coverage. Lint and strict typing pass. Full-suite results are recorded in the devlog.

The patch's live application deferred after an idle-window check found active or queued
Strata work. No service or installed application file was changed by that attempt. The
selected Strata process remains running. No vision assets were installed and no reasoning
policy, output limit or client retry setting was changed. Further remediation needs the
failing client's exact symptom and whether image input is required; the earlier functional
successes do not establish that these different request types will succeed.
