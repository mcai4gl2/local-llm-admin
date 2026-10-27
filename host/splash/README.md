# Splash host integration

Splash is opt-in and is not installed or started by this repository. MLX remains the
default. Initial profiles use 32K context, reasoning on, BF16 KV / FP32 recurrent state,
no disk cache and idle release off. GPU sidecars and other engines must be stopped.

Use the original pinned 1.4.0 standalone runtime; see
[inventory](../environment/splash.txt) and [Phase 2 plan](../../plans/20261027_splash-phase2-adapter-and-host-integration.md).
Do not use instrumented Phase 0 packages or silently substitute checkpoints.
After independently verifying the release archive checksum and unpacked original runtime,
copy `etc/runtime.example.json` to private mode-600 `etc/runtime.json`. This records that
operator audit; its presence does not replace verifying the actual downloaded archive.

## Private profile contract

Copy `etc/profiles.example.json` to private `etc/profiles.json`, replace every placeholder
with independently audited asset paths, commit revisions and asset-manifest SHA-256 values,
and set mode 600. Target and draft directories must have config and safetensors files.
Set explicit allowed asset roots; resolved symlinks must stay within them. The stock-8bit
and modified-4bit profiles have separate prepared outputs. No network/download or runtime
module import occurs when reading profiles.

Prepare each model separately with the pinned runtime's public `install.local.prepare_local_model`
interface as described in the [Phase 0 notes](../../ops/splash-phase0/README.md). Keep source
assets read-only. Its output layout is opaque; do not construct it manually. Place an
operator-managed link named `prepared/<profile-id>` to that prepared directory within the
managed `prepared` tree. Its `tokenizer` directory must exist. Prepared data needs the
pinned runtime's assembly lock/write access; it is not an independent copy of weights.

After verifying the prepared selection and asset manifests, write private
`etc/receipts/<profile-id>.json` (mode 600), matching `etc/receipt.example.json`.
The receipt binds the resolved prepared directory to target/draft revisions and digests
and the runtime archive digest. It records an operator audit, not a fresh full-weight hash.
Regenerate it when re-preparing or changing assets. Status must not hash large checkpoints.

`memory_gib` explicitly lowers Splash's automatic Metal allocation budget. It is not an OS
RSS cap and does not include all host overhead. The example's 40 GiB comes from isolated
evidence; review actual headroom before live use. The parser accepts 1–256 GiB, rejects
automatic/unbounded settings and never increases it on failure.

The admin parser at `src/local_llm_admin/splash_profiles.py` is also provided as
`splash/lib/profiles.py`. These stdlib-only copies stay byte-identical through a parity
test; the owning wrapper needs neither the admin environment nor installed runtime imports
to validate private configuration. Deploy both when changing the profile contract.

## Pinned API and launch contract

The audited [server](https://github.com/incoai/splash/blob/1.4.0/server/server.py) and
[options](https://github.com/incoai/splash/blob/1.4.0/server/serve_options.py) support:

- Python `-P -m server.server <prepared>` with `--model owner/repo` and an explicit native
  `--binary`; `--host`, `--port`, `--max-context`, `--max-memory`, `--kv-format`,
  `--state-format`, `--max-cache-disk 0`, `--idle-release off`, `--no-webui`.
- `SPLASH_API_KEY` in the private launch environment; never pass `--api-key` in argv.
  `/health` and `/ready` are public; `/status`, model lists and inference require auth.
  Host validation remains effective with authentication. `--allowed-host` is repeatable.
- `--served-model-name` is repeatable. Aliases must be nonempty slash-separated names;
  leading slashes, empty/dot path segments, whitespace and URL delimiters are rejected.
  An absolute MLX path sent by a gateway cannot be configured as a Splash alias.
- `/ready` returns `status: ready` when native readiness succeeds. `/health` is liveness.
  `/status` includes `instance.pid` and `instance.model`, effective context and native
  resource state. Model-list context/aliases and owned generations are additional checks.

Copy `etc/splash.env.example` to private mode-600 `etc/splash.env` and set a distinct key.
Files are parsed as allowlisted KEY=VALUE data, never sourced. Caller environment variables
cannot replace the file. The default bind is loopback; LAN binding is explicit.

The [capture provenance](../../tests/fixtures/splash/README.md) distinguishes recorded
functional samples from synthetic state fixtures. Loaded readiness needs verified ownership
and matching identity/context, not a health or model-list response alone. The native
Python child may recover under the same parent; neither HTTP nor an unchanged parent PID
authorizes stopping a replacement child.

## Live verification boundary

The exact existing gateway model strings, Host values and credentials remain private.
A route using an absolute model path needs an explicitly reviewed upstream model-name
change before Splash can serve it. Do not rewrite the gateway automatically or accept
unknown names. Admin-to-runtime auth tests do not qualify gateway-to-runtime auth.
Authenticated gateway inference, disconnect cancellation and a successful subsequent
request remain required live checks. No repository fixture is a live compatibility pass.

Reasoning-on passed the retained strict series; reasoning-off remains unreliable.
Stock Splash host-memory reclamation remains inconclusive; no measurement is rescored.
Live installation, runtime launch, rollout and rollback require a separately reviewed
window. See the parent plan's Phase 3 for telemetry and production adoption.


## Owning commands and reservation

`splash start <profile>` validates private runtime/assets/env/receipts before launch, then
waits for owned loaded readiness. `splash stop` revalidates every controller/native generation
before TERM and bounded KILL escalation, and waits for owned processes/listeners to exit.
`splash status [--json]` is read-only and redacts credentials and executable paths.
Reasoning defaults to high; individual off requests remain unqualified. Profiles start with
32K, no disk cache and idle release off.

The shared `bin/engine-guard.sh` serializes mutating owning-wrapper commands with the
existing owner-bearing `run/mlx.lock`. `maintenance.lock` covers only acquisition; it is
released before waiting for startup/shutdown. No wrapper invokes another owning wrapper
under the reservation. MLX keeps its existing lock implementation; Strata, ComfyUI, decision,
TTS and embeddings use the shared reservation and refuse a live/unknown Splash owner.
Deploy the helper and all corresponding wrapper changes together during an approved window.

A native child verified under the live controller can be rediscovered after internal
recovery. Recorded native generations remain stoppable after parent death. An unrecorded
or ambiguous orphan blocks activation and is never killed by a process-name/port fallback.
The wrapper refuses invalid/reused generations rather than restoring another engine over
resources it cannot establish as released. Missing process-inspection permission is unknown,
not stopped. Automatic admin recovery remains disabled for Splash.


## Admin settings and behavior

The admin's private environment uses these settings (the host env is a separate file):

| Setting | Default / meaning |
|---|---|
| `SPLASH_ENABLED` | `false`; opt in only after private inventory and an approved rollout |
| `SPLASH_ROOT` | `/Volumes/Models/splash` |
| `SPLASH_BIN` | unset; resolves to `SPLASH_ROOT/bin/splash` |
| `SPLASH_PROFILES` | unset; resolves to `SPLASH_ROOT/etc/profiles.json` |
| `SPLASH_API_BASE` | `http://127.0.0.1:8080` |
| `SPLASH_PORT` | `8080`; keep aligned with the fixed host-wrapper serving port |
| `SPLASH_API_KEY` | secret; must match the host's private key |
| `SPLASH_READY_TIMEOUT_S` | `300`, bounded at 900 seconds |
| `SPLASH_STOP_TIMEOUT_S` | `45`, bounded at 120 seconds |

The existing `ADMIN_JOB_TIMEOUT_S` bounds wrapper jobs. Disabled Splash adds no HTTP
presentation probes or selector targets. Lifecycle checks still detect a disabled owner
and permit a verified stop even if the profile inventory was removed.

The Models selector exposes explicit `splash:stock-8bit-32k` and
`splash:modified-4bit-32k` targets. Profile changes stop and restart through the same
job claim, executor and rollback path as other engines. Missing assets fail before stopping
the source engine. A failed target must release its owned processes/listener before source
restoration. External generation or profile changes require intervention.

Chat goes directly to the runtime with bearer auth and the configured served model.
The default is reasoning on with an 8192-token output allowance. Explicit reasoning off
remains explicit and unreliable. Quality supports current-engine compatibility and evals,
including external judges; model sweeps, local MLX judges and benchmarks are refused.
Results retain profile identity/fingerprint and effective 32K context. Cancellation and
deadlines close requests; external profile/generation changes prevent score promotion.

The allowlisted `splash` log source reads `logs/splash.log`, which collects controller
and inherited native output. Startup rotates files over 50 MiB and retains eight archives.
Read-only log following handles rotation/truncation without inserting usage records.
Throughput, request usage, availability qualification and gateway compatibility remain
unavailable or unverified until Phase 3; MLX measurements must not stand in for them.

## Phase 3 installation and verification handoff

Prepare a private worksheet before requesting a live window:

1. Resolve the target through `MAC_HOST` / `MAC_DEST` and the existing deploy-env
   mechanism. Review `ops/host-diff.sh` and `ops/deploy-host.sh` without `--apply`.
   Include every changed wrapper and the shared guard in one versioned diff.
2. Save private backups of the deployed wrapper set, admin environment and gateway route.
   Record original engine/profile, intent, sidecar state, process generations and successful
   original inference. Keep private receipt/env/catalog backups outside the tracked tree.
3. Audit the pinned archive and each asset, prepare separate outputs, and create the private
   receipts above. Deploying versioned files does not install these inputs or enable Splash.
4. Review the exact private gateway upstream URL, model string, Host and auth headers.
   An absolute MLX model path requires an explicit route alias change. Never add a proxy or
   silently rewrite that string. Use the supported named alias and dedicated runtime key.
5. After approval, apply the reviewed host/admin diff and opt in. Start one explicit profile,
   verify the complete process bundle and all three readiness endpoints, and run a direct
   authenticated inference. Then run the exact existing client request through the actual
   gateway with `stream=true`, the reviewed served alias and reasoning on. Check real
   content/reasoning/final usage, unknown-model refusal, Host policy and auth refusal.
   Disconnect a stream and verify a subsequent request succeeds. Test both profile IDs.
6. Verify admin chat/current-only quality, reciprocal CLI guards and the observed source
   restoration path. Review pressure/headroom without reclassifying Phase 0 measurements.
   Add telemetry only with a distinct source and proven deduplication; missing counts stay
   missing. This repository integration is not a production qualification result.

Rollback requires stopping Splash with the owning wrapper and verifying **all** recorded
and rediscovered owned generations plus the serving listener are gone. If identity is
unknown or resources remain, stop and investigate; do not start the source over them.
Restore the reviewed wrapper/admin/gateway backups and original private settings, restart
the original explicit engine/profile and sidecars through their owning commands, restore
intent, then verify authenticated gateway inference and admin health. The live worksheet
must include actual backup paths and exact approved commands before execution.

The existing process metrics tile follows the verified primary controller PID. Its RSS is
a process measurement, not the sum of controller/native memory or a host-reclamation claim.
Full bundle ownership still governs every lifecycle and quality-generation check. Broader
native memory/usage telemetry and platform qualification remain Phase 3 work.
