# One unchanged LiteLLM endpoint for every LLM engine

The shared OpenAI entry point listens on `http://mac-mini.local:8080/v1`, the existing
MLX/Strata client URL. It follows the selected verified, ready engine. LiteLLM keeps its
existing API base, public route name and upstream model ID when switching between MLX,
Strata and either Splash profile.

## Internal topology

The shared listener owns port 8080. Only native engine addresses change, behind it:

| Service | Example internal address |
|---|---|
| MLX | `http://127.0.0.1:8086` |
| Strata | `http://127.0.0.1:8087` |
| Splash | `http://127.0.0.1:8085` |

This separation prevents an inactive Splash or Strata model request from reaching MLX,
which can unload its selected model before rejecting a foreign model. No inference
runtime patch is needed. Owning-wrapper exclusion still permits only one GPU engine.
The listener itself is not a native/GPU owner and is never stopped by engine switching.

## Private configuration

Copy the [native-port example](../host/etc/engine-ports.example.json) to the Mac's
`/Volumes/Models/etc/engine-ports.json`, mode 600. The owning wrappers parse this file;
they never source it. With no file, legacy native port 8080 behavior is preserved.
A configured topology requires distinct valid ports and rejects existing service ports.
The file is private deployment configuration; the versioned deploy manifest includes
only the example and helper. It never installs or overwrites the actual configuration.

Set matching private admin settings:

```dotenv
INFERENCE_API_KEY=<the-key-already-sent-by-LiteLLM>
INFERENCE_PORT=8080
INFERENCE_HOST=0.0.0.0
INFERENCE_MODEL_ID=local-model
MLX_PORT=8086
MLX_API_BASE=http://127.0.0.1:8086
STRATA_PORT=8087
STRATA_API_BASE=http://127.0.0.1:8087
SPLASH_PORT=8085
SPLASH_API_BASE=http://127.0.0.1:8085
```

Match Splash's private `etc/splash.env` port to the topology and use a loopback native
bind. Its native API key remains the private admin `SPLASH_API_KEY`; the shared listener
supplies it. The frontend key must match the key already configured on the LiteLLM route,
so credentials do not need to change there either. An empty frontend key disables this
listener. Startup refuses a collision with a native/admin/sidecar port or a native API
base that disagrees with its configured port. It also checks the installed host helper
and private native-port file before binding; missing, invalid or mismatched host
configuration refuses startup rather than leaving wrappers aimed at the public listener.

The existing upstream ID `/Volumes/Models/models/active` remains accepted for all three
engines. `local-model` is an optional stable alias for new clients. The entry point
translates either to the selected native model and uses the caller-facing ID in responses.
`GET /v1/models` lists the stable IDs and the currently active native model. Inactive
engine-specific IDs return 404 before inference dispatch. Clients never request a new
weight load through this API.

## API behavior and bounds

Supported routes: authenticated `GET /v1/models`, `POST /v1/chat/completions` and
`POST /v1/completions`. Streaming, reasoning, usage and tool-call fields supplied by the
selected runtime pass through. Native runtimes remain responsible for capabilities and
context limits; selecting Splash does not add vision. Admin cookies do not authorize
inference, and an inference key grants no access to admin control routes.

No ready engine, unknown ownership, conflicting engines, a disabled backend or an active
engine operation returns 503. There is no engine start, fallback, replay or automatic
retry. Native errors retain their HTTP status with sanitized text. Streamed model fields
are translated incrementally; the response is never collected before streaming begins.
Disconnect closes upstream and releases admission. At most two requests are admitted;
excess requests receive 429. Bodies/non-streaming responses are limited to 8 MiB and an
individual streamed event to 2 MiB. No per-token database writes or duplicated billed
usage records are introduced.

## Migration and rollback

During an approved quiet window, record the original engine/service intent, freeze new
jobs and stop the admin/watchdog so it cannot restart an engine during migration. Stop
the current native engine through its owner and prove its processes and listener absent.
Back up the deployed source, private admin/native environments and native port configuration.
Deploy the paired host/admin changes, install the private topology and matching admin
settings. Start the original selected native engine on its internal port, then start
the admin and shared listener before unfreezing jobs. The native engine moves
behind the listener while the external URL, request model and LiteLLM route stay unchanged.

Verify original inference, each selected engine/profile, both streaming and non-streaming
responses, invalid-key/model refusal, cancellation and owned cleanup. Keep existing
qualification failures and old results scoped to their original deployed revision.
A branch update performs none of these live changes. Roll back by stopping the new
listeners/owners and restoring the matched source/configuration and original native port
before restarting the original engine. Do not bind the shared listener and a native
engine on 8080 simultaneously. The historical rollout reports do not qualify this new
entry point live.
