Overall Progress: 100%

# Repair live inference after engine switching

## Problem and evidence

The Mac still runs the pre-shared-listener deployment. Strata completed requests, but
five-token requests exhausted their output on reasoning and produced no final answer.
Splash loaded successfully on native port 8085 while the existing LiteLLM route still
used 8080. MLX subsequently loaded the selected local model successfully, then a request
for model `x` displaced it and failed an offline Hub lookup. Process health stayed OK
with no loaded model. The configured LiteLLM route uses the stable active-model ID;
the origin of the foreign-model requests remains unproven.

## Hypotheses and reproduction

1. Confirmed: the deployment lacks the merged shared frontend and private native-port
   configuration, so switching to Splash disconnects the existing client route.
2. Confirmed: foreign-model dispatch to the native MLX API can unload selected weights.
   Logs show the unload, attempted `x` load and offline failure. Do not repeat this
   destructive probe against the unprotected native endpoint.
3. Bounded: Strata's short output limits can yield reasoning without an answer. No
   recent native crash is demonstrated; do not change runtime policy without proof.

Private SSH captures preserve original logs, jobs, settings and request results. A
correct stable-ID request restored MLX and returned HTTP 200 with an answer. Current
source always builds admin-chat requests from the selected backend descriptor.

## Fix and validation

- [x] 🟩 Collect logs, deployment identity and read-only job history; recover selected MLX.
- [x] 🟩 Run existing shared listener, startup, native-port and owner regression tests.
- [x] 🟩 Prepare a revision-pinned paired source/configuration migration with backups,
  unchanged LiteLLM URL/model/key, preflight checks and a rollback procedure.
- [x] 🟩 Stop the admin/watchdog and current native owner during migration; install the
  matched host/admin files and private topology, then restore original engine intent.
- [x] 🟩 Verify authenticated stable-ID streaming/non-streaming inference, rejection of
  foreign models without weight unload, admin chat and unchanged sidecar intent.
- [x] 🟩 Verify switching through the shared endpoint and restore the original engine;
  archive this plan and append public-safe results to the devlog.

## Safety and rollback

The user requested fixing the confirmed live failure. Preserve weights, runtime builds,
private credentials, existing qualification outcomes and sidecar intent. No new model
or runtime download is required. Back up matched app/host/configuration and SQLite state
before stopping services; restore those together if the migration fails. Stop new
listeners before restoring native legacy port 8080. Keep raw logs and deployment details
outside git. Do not attribute request origin or claim broader Strata qualification.


## Outcome

Deployed merged revision `68342fc3909008643703c9a923a8ca01ec8fcc03` with matched backups,
unchanged dependencies and the existing LiteLLM URL/model/key. All 127 focused regression
tests passed. Live stable-ID streaming, non-streaming, model refusal and admin chat passed
for Strata, both Splash profiles and MLX. The actual LiteLLM proxy route returned an answer.
Invalid-key rejection, preserved native weights, stream-disconnect recovery, installed
payload hashes and restoration of original engine/sidecar intent were verified. The initial
stream-verification harness needed to accept valid null content frames; no production fix
was required. Request origin remains unproven, and no broader Strata crash or performance
claim is made. See the [deployment report](../../../docs/reports/shared-inference-20261029.md).
