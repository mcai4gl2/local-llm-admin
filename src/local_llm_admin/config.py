"""Settings, read from the environment (``~/.config/local-llm-admin/env`` on the Mac)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Entries ending in "/" are prefixes: every weights dir behind the local route counts.
# The other entries are the model ids Strata serves for its two installed packs (Q2_0, IQ3_XXS).
DEFAULT_LOCAL_MODELS = (
    "openai//Volumes/Models/models/,openai/qwen3.8-flash-next-q2_0,"
    "openai/qwen3.8-flash-next-iq3_xxs"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", validate_assignment=True)

    # MLX side (read-only except through the mlx-model CLI)
    mlx_root: Path = Path("/Volumes/Models")
    mlx_model_bin: Path | None = None
    mlx_root_must_be_mount: bool = True
    mlx_api_base: str = "http://127.0.0.1:8080"
    chat_ui_port: int = 7860
    mlx_port: int = 8080

    # ComfyUI (driven only through its CLI and HTTP API; its venv is never used)
    comfyui_root: Path = Path("/Volumes/Models/comfyui")
    comfyui_bin: Path | None = None
    comfyui_api_base: str = "http://127.0.0.1:8188"
    comfyui_public_url: str = "http://127.0.0.1:8188"
    comfyui_port: int = 8188
    comfyui_ready_timeout_s: float = 120.0
    # output browsing (M5): bounded scans of comfyui/output
    comfyui_output_scan_max: Annotated[int, Field(ge=100, le=200_000)] = 20_000
    comfyui_output_scan_depth: Annotated[int, Field(ge=0, le=16)] = 6
    comfyui_output_timeout_s: Annotated[float, Field(gt=0.0, le=120.0)] = 15.0
    comfyui_output_cache_s: Annotated[float, Field(ge=0.0, le=600.0)] = 15.0
    comfyui_history_items: Annotated[int, Field(ge=0, le=1000)] = 100
    engine_stop_timeout_s: float = 30.0

    # Strata (Qwen3.8-Flash-Next on Metal; driven only through its wrapper and HTTP API)
    strata_enabled: bool = False
    strata_root: Path = Path("/Volumes/Models/strata")
    strata_bin: Path | None = None
    # MLX's port: LiteLLM's one route reaches whichever engine runs
    strata_api_base: str = "http://127.0.0.1:8080"
    strata_port: int = 8080
    strata_ready_timeout_s: float = 300.0  # a cold start reads ~38-47 GB
    # STRATA_API_KEY from strata/etc/strata.env; empty when Strata runs without a key, like MLX
    strata_api_key: SecretStr = SecretStr("")

    # Admin
    admin_password_hash: SecretStr = SecretStr("")
    admin_session_secret: SecretStr = SecretStr("")
    admin_session_max_age_s: int = 7 * 24 * 3600
    admin_cookie_secure: bool = False
    admin_data_dir: Path = Path("data")
    admin_log_dir: Path = Path("logs")
    admin_job_timeout_s: float = 300.0
    admin_login_max_failures: int = 5
    admin_login_window_s: int = 300

    # Resilience (M1): diagnostics, alerts and the MLX watchdog
    probe_timeout_s: float = 3.0
    monitor_interval_s: float = 15.0
    litellm_check_interval_s: float = 60.0
    watchdog_enabled: bool = True
    watchdog_grace_s: float = 30.0
    watchdog_crash_window_s: float = 1800.0
    alert_retention_days: int = 90

    # Quality runs (M2): per-request and whole-run bounds, RSS sampling, retention
    quality_request_timeout_s: float = 300.0
    quality_run_deadline_s: float = 4 * 3600.0
    quality_ready_timeout_s: float = 60.0
    quality_rss_interval_s: float = 0.5
    quality_retention_days: int = 90

    # Observability (M4): request history, availability samples, log rotation
    availability_interval_s: Annotated[float, Field(ge=10.0, le=600.0)] = 60.0
    observability_retention_days: Annotated[int, Field(ge=7, le=365)] = 90
    perf_slow_ttft_s: Annotated[float, Field(gt=0.0, le=600.0)] = 10.0
    log_rotate_max_mib: Annotated[int, Field(ge=1, le=10240)] = 50
    log_rotate_keep: Annotated[int, Field(ge=1, le=50)] = 8
    log_rotate_hour: Annotated[int, Field(ge=0, le=23)] = 4  # ops-timezone hour of the daily run

    # Model lifecycle (M3)
    inventory_timeout_s: Annotated[float, Field(gt=0.0, le=300.0)] = 20.0
    # Hugging Face owners whose repositories may be downloaded; "*" allows any
    download_allowed_owners: str = "mlx-community"
    download_cli_timeout_s: Annotated[float, Field(gt=0.0, le=600.0)] = 60.0

    # Decision sidecar (M6): OpenJev on DiffusionGemma, driven only through
    # decision/bin/openjev and its loopback API; off unless DECISION_ENABLED=true
    decision_enabled: bool = False
    decision_root: Path = Path("/Volumes/Models/decision")
    decision_bin: Path | None = None
    decision_api_base: str = "http://127.0.0.1:8081"
    decision_port: int = 8081
    decision_api_key: SecretStr = SecretStr("")  # the sidecar's own key (etc/openjev.env)
    decision_ready_timeout_s: Annotated[float, Field(gt=0.0, le=900.0)] = 120.0
    decision_request_timeout_s: Annotated[float, Field(gt=0.0, le=300.0)] = 10.0
    # measured peak phys_footprint with OPENJEV_MLX_CACHE_LIMIT_GB=2 (Phase 0, R6)
    decision_footprint_gb: Annotated[float, Field(gt=0.0, le=256.0)] = 20.0
    decision_headroom_gb: Annotated[float, Field(ge=0.0, le=64.0)] = 6.0
    # MLX models that may run next to the sidecar (comma-separated registered names)
    decision_allowed_mlx: str = ""
    # LAN gateway (M6 Phase 5): per-client keys in front of the loopback sidecar
    decision_gateway_host: str = "0.0.0.0"
    # false: keys are optional (LAN use); a valid key still attributes usage to its client
    decision_gateway_require_key: bool = False
    decision_gateway_port: int = 8082
    decision_gateway_max_inflight: Annotated[int, Field(ge=1, le=64)] = 4
    decision_gateway_client_inflight: Annotated[int, Field(ge=1, le=64)] = 2

    # TTS sidecar (M7): Qwen3-TTS on mlx-audio, driven only through tts/bin/qwen-tts and
    # its loopback API; off unless TTS_ENABLED=true. Values marked (Phase 0) come from the
    # M7 survey record (pre-split design notes; mlx-audio 0.5.7, 2026-10-04).
    tts_enabled: bool = False
    tts_root: Path = Path("/Volumes/Models/tts")
    tts_bin: Path | None = None
    tts_api_base: str = "http://127.0.0.1:8083"
    tts_port: int = 8083
    tts_api_key: SecretStr = SecretStr("")  # mlx-audio 0.5.7 has no key; loopback only (Phase 0)
    tts_ready_path: str = "/v1/models"  # (Phase 0)
    tts_ready_timeout_s: Annotated[float, Field(gt=0.0, le=900.0)] = 120.0
    # streamed requests peak at 6.2-6.8 GB phys_footprint, flat in length (Phase 0)
    tts_footprint_gb: Annotated[float, Field(gt=0.0, le=256.0)] = 8.0
    tts_allowed_mlx: str = ""
    tts_model_id: str = "qwen3-tts"  # what clients send as "model"
    tts_upstream_model: str = (
        "/Volumes/Models/tts/models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"  # (Phase 0)
    )
    # the model's speakers (Phase 0). Keep the list: mlx-audio answers an unknown name
    # with the default speaker instead of an error. Empty accepts any valid name.
    tts_voices: str = "Vivian,Serena,Uncle_Fu,Dylan,Eric,Ryan,Aiden,Ono_Anna,Sohee"
    tts_default_voice: str = "Vivian"
    tts_max_input_chars: Annotated[int, Field(ge=100, le=100_000)] = 10_000
    tts_client_max_input_chars: Annotated[int, Field(ge=1, le=100_000)] | None = None
    # a chunk's text_weight (CJK counts 4): ~55 s of speech in either language (Phase 0)
    tts_chunk_chars: Annotated[int, Field(ge=100, le=10_000)] = 800
    tts_timeout_base_s: Annotated[float, Field(gt=0.0, le=600.0)] = 30.0
    # per unit of text_weight: measured 0.014-0.019 s, plus room for a chunk cut off at
    # its token cap (Phase 0)
    tts_timeout_per_char_s: Annotated[float, Field(gt=0.0, le=10.0)] = 0.04
    tts_ffmpeg: Path = Path("/opt/homebrew/bin/ffmpeg")

    # Embedding service (M8): Qwen3-Embedding-4B + Qwen3-Reranker-0.6B in their own MLX-VLM
    # process, driven only through embed/bin/qwen-embed and its loopback API. Always on (not
    # a slot sidecar); off unless EMBED_ENABLED=true. (Phase 0) values come from the M8
    # survey record (pre-split design notes).
    embed_enabled: bool = False
    embed_root: Path = Path("/Volumes/Models/embed")
    embed_bin: Path | None = None
    embed_api_base: str = "http://127.0.0.1:8084"
    embed_port: int = 8084
    embed_cmdline_marker: str = "mlx_vlm.server"  # fallback B: "embed/app/server.py"
    embed_ready_path: str = "/health"  # healthy once both models are loaded
    embed_ready_timeout_s: Annotated[float, Field(gt=0.0, le=900.0)] = 180.0
    # Phase 0: 8.1 GB after 32-input batches (13 GB after 128: MLX keeps its buffer cache)
    embed_footprint_gb: Annotated[float, Field(gt=0.0, le=256.0)] = 9.0
    embed_allowed_mlx: str = ""
    embed_model_id: str = "qwen3-embed"  # what clients send as "model"
    embed_upstream_model: str = "/Volumes/Models/embed/models/Qwen3-Embedding-4B-8bit"
    embed_dims: Annotated[int, Field(ge=1, le=8192)] = 2560  # the model's full width (tests use 8)
    rerank_model_id: str = "qwen3-rerank"
    rerank_upstream_model: str = "/Volumes/Models/embed/models/Qwen3-Reranker-0.6B"
    embed_max_in_flight: Annotated[int, Field(ge=1, le=16)] = 2
    embed_max_inputs: Annotated[int, Field(ge=1, le=2048)] = 32  # caps the footprint above
    embed_max_input_chars: Annotated[int, Field(ge=1, le=200_000)] = 32_000
    rerank_max_documents: Annotated[int, Field(ge=1, le=2000)] = 200
    embed_timeout_base_s: Annotated[float, Field(gt=0.0, le=600.0)] = 30.0
    embed_timeout_per_input_s: Annotated[float, Field(gt=0.0, le=60.0)] = 0.2  # p95 0.048 x 3
    # Gap after each upstream call: back-to-back batches cut chat decode by ~88% (Phase 0);
    # 500 ms still cost 67%, 1500 ms 37% (D5, 2026-10-10)
    embed_min_interval_ms: Annotated[int, Field(ge=0, le=60_000)] = 1500  # live: 58 tok/s chat under load

    # Metrics
    metrics_interval_s: float = 5.0
    metrics_history_points: int = 720

    # LiteLLM usage
    litellm_base_url: str = "http://127.0.0.1:4000"
    litellm_admin_key: SecretStr = SecretStr("")
    litellm_local_models: str = DEFAULT_LOCAL_MODELS
    # ops timezone (Asia/Hong_Kong); JavaScript getTimezoneOffset convention: -480 = UTC+8
    litellm_timezone_offset_min: int = -480
    litellm_timeout_s: float = 10.0
    litellm_cache_ttl_s: float = 60.0

    @property
    def mlx_model_path(self) -> Path:
        return self.mlx_model_bin or self.mlx_root / "bin" / "mlx-model"

    @property
    def comfyui_path(self) -> Path:
        return self.comfyui_bin or self.comfyui_root / "bin" / "comfyui"

    @property
    def strata_path(self) -> Path:
        return self.strata_bin or self.strata_root / "bin" / "strata"

    @property
    def decision_path(self) -> Path:
        return self.decision_bin or self.decision_root / "bin" / "openjev"

    @property
    def decision_allowed(self) -> set[str]:
        return {s.strip() for s in self.decision_allowed_mlx.split(",") if s.strip()}

    @property
    def tts_path(self) -> Path:
        return self.tts_bin or self.tts_root / "bin" / "qwen-tts"

    @property
    def embed_path(self) -> Path:
        return self.embed_bin or self.embed_root / "bin" / "qwen-embed"

    @property
    def tts_voice_list(self) -> list[str]:
        return [v.strip() for v in self.tts_voices.split(",") if v.strip()]

    @property
    def local_model_strings(self) -> list[str]:
        return [s.strip() for s in self.litellm_local_models.split(",") if s.strip()]

    @property
    def run_dir(self) -> Path:
        return self.mlx_root / "run"

    @property
    def mlx_log_dir(self) -> Path:
        return self.mlx_root / "logs"
