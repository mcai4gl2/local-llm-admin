# Local MLX models

Everything heavy lives on this external volume, mounted at `/Volumes/Models`.
Only one model process is allowed at a time.

The drive must be mounted before any command below will work.

## Layout

| Path | What it is |
|---|---|
| `/Volumes/Models/models/` | Model weights |
| `/Volumes/Models/models/active` | Symlink to the model currently selected |
| `/Volumes/Models/etc/models.conf` | Short name, tab, model directory |
| `/Volumes/Models/venvs/mlx` | Python 3.12 virtualenv |
| `/Volumes/Models/python` | Python install used by that venv |
| `/Volumes/Models/logs/mlx-server.log` | API server log |
| `/Volumes/Models/run/active.env` | Last selected model |
| `/Volumes/Models/bin/mlx-model` | Start, stop, and switch |

`uv` itself is installed with Homebrew on the internal disk. The venv, Python, and weights are not.

## Daily commands

Run these from this Mac. `mlx-model` is not on `PATH`; use the full path or `cd` here.

```sh
/Volumes/Models/bin/mlx-model list
/Volumes/Models/bin/mlx-model status
/Volumes/Models/bin/mlx-model switch qwen3.8-27b
/Volumes/Models/bin/mlx-model stop
/Volumes/Models/bin/mlx-model start
/Volumes/Models/bin/mlx-model select qwen3.8-27b
```

`switch` stops whatever is running, points `models/active` at the chosen weights, and starts the API. `start` with no name restarts the last selected model. `select` changes the selected model without loading it. It works only while no MLX or ComfyUI process is running; the admin's quality runs use it to put back the selection after a run that started from off. A `*` in `list` marks the selected name, not necessarily a running process. Use `status` for that.

A command that fails while holding `run/mlx.lock` (for example an unknown name) releases it. zsh skips the EXIT trap when errexit fires inside a function, so a ZERR trap does the cleanup.

Do not start `mlx_vlm.server` or the chat UI by hand. A second process will load another copy of the weights.

The decision sidecar (`/Volumes/Models/decision/bin/openjev`, port 8081) is not managed by `mlx-model` and may run next to the MLX server. See `/Volumes/Models/decision/README.md`.

## Add a model

Download the MLX weights onto this volume, then register the directory. It must contain `config.json`.

```sh
/Volumes/Models/bin/mlx-model add qwen3.6-27b /Volumes/Models/models/Qwen3.6-27B-4bit
/Volumes/Models/bin/mlx-model switch qwen3.6-27b
```

Names may contain letters, numbers, dots, underscores, and dashes. `models.conf` is tab-separated. Do not edit it with spaces between the name and the path.

## API and LiteLLM

The server listens on all interfaces, port 8080:

- Base URL: `http://mac-mini.local:8080/v1`
- Model id: `/Volumes/Models/models/active`

Use that stable id in LiteLLM. Do not use the real directory name. Switching models does not change the id.

```yaml
model_list:
  - model_name: local-qwen
    litellm_params:
      model: openai//Volumes/Models/models/active
      api_base: http://mac-mini.local:8080/v1
      api_key: none
      timeout: 300

litellm_settings:
  drop_params: true
  request_timeout: 300
```

The double slash is required. LiteLLM removes the `openai/` prefix and sends `/Volumes/Models/models/active` upstream. A different model string can make this server try to load another model.

There is no API key. Anything that can reach port 8080 can use the GPU. Keep it on the LAN, or use Tailscale. Do not port-forward it.

Check from another machine:

```sh
curl http://mac-mini.local:8080/v1/models
curl http://mac-mini.local:8080/health
```

`/health` should show `"loaded_model": "/Volumes/Models/models/active"`.

The server decodes one request at a time (`--max-num-seqs 1`). Other requests wait.

## Chat UI

The browser UI is separate and also loads the model, so stop the API first. The chat script does that.

```sh
/Volumes/Models/bin/chat-qwen.sh
```

Open `http://mac-mini.local:7860` from another machine on the LAN. There is no login. Stop it before starting the API again:

```sh
/Volumes/Models/bin/mlx-model stop
/Volumes/Models/bin/mlx-model start
```

`serve-qwen.sh` is a wrapper for `mlx-model switch qwen3.8-27b`.

## Context

Qwen3.8-27B-4bit has a native window of 262,144 tokens. No smaller server cap is set. If a client omits `max_tokens`, the completion default is 2,048. That is not the context limit.

On this 64 GB Mac, 128k is the comfortable size for one request. 256k fits but is slower and leaves less memory for macOS. Only 16 of the 64 layers grow a KV cache, at about 64 KB per token.

## If it fails

```sh
tail -50 /Volumes/Models/logs/mlx-server.log
/Volumes/Models/bin/mlx-model status
```

If a command says another `mlx-model` command is running, but nothing is:

```sh
/Volumes/Models/bin/mlx-model unlock
```

`unlock` removes `run/mlx.lock` only when the command that took it is gone. It refuses when that command is still running, or when it cannot tell who owns the lock. Commands release their own lock on success, failure, Ctrl-C and `kill`; only `kill -9` or a crash leaves one behind. Do not `rmdir` the lock by hand while any `mlx-model` command is running.

### Maintenance gate

`run/maintenance.lock` is held for a moment while a command takes the lock, while `unlock` checks it, and for the whole of an `ops/deploy-host.sh` deployment. Commands wait up to 5 seconds for it, then print `Maintenance in progress` with the owner's pid and command.

If a gate is left behind (its owner was killed with `kill -9`):

1. Read the owner: `cat /Volumes/Models/run/maintenance.lock/owner`.
2. Check that the process is gone: `ps -o lstart= -p <pid>` prints nothing, or prints a start time different from `start=` (the pid was reused).
3. Only then remove it: `rm /Volumes/Models/run/maintenance.lock/owner && rmdir /Volumes/Models/run/maintenance.lock`.

## Log rotation

```sh
/Volumes/Models/bin/mlx-model rotate-logs [--max-bytes N] [--keep K]
```

Rotates `logs/mlx-server.log`, `chat-qwen.log`, `serve-qwen.log` and `download-*.log` when a log is at least `--max-bytes` (default 50 MiB, allowed 1 MiB to 10 GiB). The log is renamed to `<name>.YYYYmmdd-HHMMSS`, and a new empty file with the same permissions takes its place. At most `--keep` archives (default 8, allowed 1 to 50) are kept per log; older ones are deleted.

A log is only renamed while nothing writes to it: the MLX server for the MLX logs, a download script for download logs, and any process `lsof` shows holding the file. A log with a running or unverifiable writer is reported as `deferred` and left alone. Logs are never copied and truncated, which would lose lines. Symlinks and any other names are refused.

The command takes `run/mlx.lock` like `start` and `stop`. Output is one line per file (`rotated`, `deferred`, `skipped`, `missing`, `pruned`, `error`); the exit status is 1 if any file had an error, and the original log is kept in that case.

`mlx-model start` also rotates `mlx-server.log` before it starts the server, while the server is stopped. It uses the defaults above, or `MLX_LOG_ROTATE_MAX_BYTES` and `MLX_LOG_ROTATE_KEEP` from the environment. A failed rotation there never stops the start. The admin runs `rotate-logs` once a day with its own settings (`LOG_ROTATE_MAX_MIB`, `LOG_ROTATE_KEEP`, `LOG_ROTATE_HOUR`). ComfyUI logs are not rotated by this command.

The volume must stay mounted while the server is running. After unplugging it, mount it again and run `mlx-model start`.

## Remove a model

```sh
/Volumes/Models/bin/mlx-model remove <name>                  # unregister, keep the files
/Volumes/Models/bin/mlx-model remove <name> --delete-files   # also delete the directory
/Volumes/Models/bin/mlx-model remove-recover
```

`remove` refuses the selected model, the directory `models/active` points at, and a name listed more than once. It takes `run/mlx.lock`. The registry is copied to `etc/backups/models.conf.<stamp>` (the newest 20 are kept) and then replaced in one rename.

`--delete-files` only deletes a real directory directly under `models/` that no other name uses; symlinks, paths through a symlink and directories elsewhere are refused (unregister those instead). The directory is first renamed into `quarantine/<id>` on the same volume, with `quarantine/<id>.journal` beside it. If the registry cannot be rewritten, the directory is renamed back. Only after the registry update is the quarantined copy deleted. If that last step fails, the output says `cleanup-pending <id>`; the model is already unregistered and `remove-recover` finishes the deletion. `remove-recover` also undoes a removal interrupted before the registry changed, and never overwrites a directory that exists again at the original path. The admin passes `--expect-path` and `--expect-inode` from its preview, so a directory replaced in between is refused.

Deleted weights cannot be restored by any rollback; prefer unregistering.

## Download a model

```sh
/Volumes/Models/bin/mlx-model download-preview mlx-community/<repo> [revision]
/Volumes/Models/bin/mlx-model download mlx-community/<repo> <commit-sha> <dir-name> <run-id>
/Volumes/Models/bin/mlx-model download-status [run-id]
/Volumes/Models/bin/mlx-model download-cancel <run-id>
/Volumes/Models/bin/mlx-model download-resume <run-id>
/Volumes/Models/bin/mlx-model add <name> /Volumes/Models/models/<dir-name>
```

The work is done by `bin/mlx-download`, a standard-library Python script run with the system `/usr/bin/python3` (3.9 or newer); it needs no packages and never uses the MLX venv. `download-preview` prints JSON: the commit hash the revision resolves to, every file with its size and checksum, the free space and the model card as plain text. `download` takes that full commit hash, so retries and resumes always fetch the same files, a run id of 12 lowercase letters or digits (the admin makes one), and a new directory name.

A run downloads into `downloads/<run-id>/files/` (not listed as a model), fetching small files first so a shard missing from the index is found early. Each file is fetched to `<file>.part` with HTTP range requests, retried up to 4 times, and must match the size and the checksum Hugging Face publishes (sha256 for LFS files, the git blob hash otherwise) before it gets its name. When every file is verified, the helper calls `mlx-model publish-download`, which takes `run/mlx.lock` and renames the directory to `models/<dir-name>`. It never replaces an existing directory. Register it with `add` afterwards. The free space needed is the remaining bytes plus 5% (at least 2 GiB).

One download runs at a time (`run/download.lock`, holding the downloader's pid and start time); it never holds `run/mlx.lock` while downloading, so models can be switched meanwhile. `downloads/<run-id>/status.json` holds the state (`running`, `publishing`, `published`, `failed`, `cancelled`; `download-status` reports `interrupted` when the downloader is gone, for example after a reboot). `download-cancel` signals only the recorded downloader process, after checking its start time and command. Cancelled, failed and interrupted runs keep their partial files, and `download-resume` continues them. Nothing downloaded is ever deleted automatically: remove `downloads/<run-id>/` by hand when it is no longer needed.

The log is `logs/download-<run-id>.log`: `===== <time> start =====`, a `GET <repo>/<file>` line per file, and one `===== <time> done fail=0|1 =====` when a run ends published or failed (the admin raises one alert for it). For gated or private repositories, accept the terms on huggingface.co and save a read token to `/Volumes/Models/hf/token` (or set `HF_TOKEN`). The token is sent only to huggingface.co, never to the CDN a file redirects to, and is never logged.

## Per-model settings (profiles)

```sh
/Volumes/Models/bin/mlx-model profile schema
/Volumes/Models/bin/mlx-model profile show <name>
/Volumes/Models/bin/mlx-model profile set <name> '{"launch": {"max_kv_size": 32768}, "chat": {"temperature": 0.3}}'
/Volumes/Models/bin/mlx-model profile reset <name>     # back to built-in flags
/Volumes/Models/bin/mlx-model profile revert <name>    # the version before the last change
```

A profile is `etc/profiles/<name>.json`, read and validated by `bin/mlx-profile` (standard-library Python on `/usr/bin/python3`); it is data, never sourced by a shell. `schema` lists every allowed field with its type, range or allowed values; anything else is refused, including API keys, draft models, adapter paths, template text and raw flags.

- `launch` fields become `mlx_vlm.server` flags (`--max-kv-size`, `--prefill-step-size`, `--max-tokens`, `--enable-thinking`, `--thinking-budget`, `--kv-bits`, `--kv-quant-scheme`, `--kv-group-size`, `--quantized-kv-start`, `--vision-cache-size`; mlx-vlm 0.7.3). `start` and `switch` apply them, so a change needs a restart of that model; `set` never restarts anything. `max_tokens`, `enable_thinking` and `thinking_budget` are server defaults for every client that does not set them, LiteLLM included. `--max-num-seqs 1`, host, port, model path and log level stay fixed.
- `chat` fields are defaults for the admin's test chat only.

`set`, `reset` and `revert` take `run/mlx.lock` and work only for registered names. Each write is a temp file plus rename and keeps the previous version as `<name>.json.prev`. `start` records the fingerprint of the flags it used as `MLX_PROFILE_FINGERPRINT` in `run/active.env` (`default` for none). A missing helper or an invalid profile never stops a start: the model starts with the built-in flags, the fingerprint is `default-fallback`, and the reason is written to `mlx-server.log`.
