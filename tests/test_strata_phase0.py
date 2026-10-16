"""ops/strata-phase0/capture.sh: scrubbing and request headers, with a fake curl."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "ops/strata-phase0/capture.sh"

# prints its argv (one per line, as JSON) plus a body with an address and a version string
FAKE_CURL = r"""#!/bin/bash
python3 -c 'import json,sys; print(json.dumps({"argv": sys.argv[1:], "ip": "10.1.2.3", "local": "127.0.0.1", "version": "0.1.44.1-metal", "path": sys.argv[0]}))' "$@"
"""


def _capture(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    sandbox = tmp_path / "sandbox"
    (sandbox / "logs").mkdir(parents=True)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "STRATA_API_KEY": "k" * 40}
    r = subprocess.run(["zsh", str(CAPTURE), str(sandbox)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return sandbox / "results/fixtures"


def test_capture_scrubs_addresses_but_keeps_version_strings(tmp_path):
    doc = json.loads((_capture(tmp_path) / "metrics.json").read_text())
    assert doc["ip"] == "192.0.2.1"
    assert doc["local"] == "127.0.0.1"
    assert doc["version"] == "0.1.44.1-metal"


def test_capture_sends_accept_and_auth_as_separate_header_arguments(tmp_path):
    argv = json.loads((_capture(tmp_path) / "metrics.prom").read_text())["argv"]
    i = argv.index("Accept: text/plain")
    assert argv[i - 1] == "-H"
    j = argv.index("Authorization: Bearer " + "k" * 40)
    assert argv[j - 1] == "-H"


def _bench():
    import importlib.util

    spec = importlib.util.spec_from_file_location("strata_bench", ROOT / "ops/strata-phase0/bench.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bench_needle_cases_hide_the_code_at_each_depth_within_the_token_budget():
    bench = _bench()
    found = bench.needle_cases(220_000)
    assert [name for name, _, _, _ in found] == ["needle-220k-10", "needle-220k-50", "needle-220k-90"]
    for name, prompt, max_tokens, expect in found:
        assert prompt.count(expect) == 1 and max_tokens <= 128
        depth = prompt.index(expect) / len(prompt)
        assert abs(depth - int(name.rsplit("-", 1)[1]) / 100) < 0.02
        # ~1.21 tokens per filler word (Phase 0: 24,000 words -> 29,029 tokens)
        assert 0.8 * 220_000 < len(prompt.split()) * 1.21 < 220_000


def test_bench_needle_found_is_case_and_space_insensitive():
    bench = _bench()
    assert bench.needle_found("The code is  7391-alpha.", "7391-ALPHA")
    assert not bench.needle_found("I could not find it.", "7391-ALPHA")


def test_bench_turns_thinking_off_the_way_strata_reads_it():
    # Strata ignores a top-level enable_thinking; it reads chat_template_kwargs / reasoning_effort
    off = _bench().request_body("q", 64, think=False)
    assert off["chat_template_kwargs"] == {"enable_thinking": False}
    assert off["reasoning_effort"] == "none"
    on = _bench().request_body("q", 64, think=True)
    assert "chat_template_kwargs" not in on and "reasoning_effort" not in on


def test_bench_needle_with_thinking_leaves_room_to_think_and_answer():
    bench = _bench()
    found = bench.needle_cases(8000, think=True)
    assert [n for n, _, _, _ in found] == ["needle-8k-10-think", "needle-8k-50-think", "needle-8k-90-think"]
    assert all(max_tokens >= 8192 for _, _, max_tokens, _ in found)
    assert all(m <= 128 for _, _, m, _ in bench.needle_cases(8000))
