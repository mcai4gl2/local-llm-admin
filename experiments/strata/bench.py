"""Strata Phase 0 bench. Stdlib only; runs on the Mac against the sandboxed server.

usage: bench.py <label> [--base http://127.0.0.1:8086] [--out results/] [--long] [--needle N]
Streams chat completions and prints one JSON line per case: TTFT, prompt and decode tok/s,
plus memory pressure, swap and the engine's resident memory sampled during the request.
--long adds the 32K prompt (minutes per run at 64 GB; off by default).
--needle N runs only the long-context check: a code hidden at 10%, 50% and 90% depth of a
prompt of about N tokens, thinking off; each row says whether the answer names it.
--think keeps thinking on for --needle (the model's default effort, room for 8192 tokens).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

KEY = os.environ.get("STRATA_API_KEY", "")
FILLER = (
    "The archive held ledgers from the harbour office: ship names, cargo weights, the "
    "hour each vessel cleared the breakwater and the name of the pilot who took her out. "
)


def _sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _swap_mb() -> float:
    # "total = 2048.00M  used = 844.69M  free = ..."
    parts = _sh("sysctl", "-n", "vm.swapusage").split()
    try:
        return float(parts[parts.index("used") + 2].rstrip("M"))
    except (ValueError, IndexError):
        return -1.0


def _engine_rss_mb() -> float:
    pids = _sh("pgrep", "-x", "strata").split()
    total = 0
    for pid in pids:
        out = _sh("ps", "-o", "rss=", "-p", pid).strip()
        total += int(out) if out.isdigit() else 0
    return total / 1024


class Sampler(threading.Thread):
    """Worst pressure level (1 normal, 2 warn, 4 critical), peak swap and engine RSS."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.pressure = 0
        self.swap_start = _swap_mb()
        self.swap_peak = self.swap_start
        self.rss_peak = 0.0

    def run(self) -> None:
        while not self.stop.is_set():
            lvl = _sh("sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip()
            self.pressure = max(self.pressure, int(lvl) if lvl.isdigit() else 0)
            self.swap_peak = max(self.swap_peak, _swap_mb())
            self.rss_peak = max(self.rss_peak, _engine_rss_mb())
            self.stop.wait(0.5)

    def result(self) -> dict[str, Any]:
        self.stop.set()
        self.join(2)
        return {
            "pressure_max": self.pressure,
            "swap_start_mb": round(self.swap_start, 1),
            "swap_peak_mb": round(self.swap_peak, 1),
            "engine_rss_peak_mb": round(self.rss_peak, 1),
        }


def request_body(prompt: str, max_tokens: int, think: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "strata",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if not think:  # Strata ignores a top-level enable_thinking (the MLX spelling)
        body["chat_template_kwargs"] = {"enable_thinking": False}
        body["reasoning_effort"] = "none"
    return body


def stream(base: str, prompt: str, max_tokens: int, think: bool = True) -> dict[str, Any]:
    body = request_body(prompt, max_tokens, think)
    req = urllib.request.Request(base + "/v1/chat/completions", json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if KEY:
        req.add_header("Authorization", f"Bearer {KEY}")
    t0 = time.perf_counter()
    first: float | None = None
    chunks = 0
    text: list[str] = []
    usage: dict[str, Any] = {}
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            d = json.loads(line[5:])
            if d.get("usage"):
                usage = d["usage"]
            for c in d.get("choices") or []:
                delta = c.get("delta") or {}
                text.append(delta.get("content") or "")
                if delta.get("content") or delta.get("reasoning_content"):
                    chunks += 1
                    if first is None:
                        first = time.perf_counter()
    end = time.perf_counter()
    ttft = (first or end) - t0
    out_tokens = int(usage.get("completion_tokens") or chunks)
    in_tokens = int(usage.get("prompt_tokens") or 0)
    decode_s = max(end - (first or end), 1e-9)
    return {
        "prompt_tokens": in_tokens,
        "completion_tokens": out_tokens,
        "ttft_s": round(ttft, 3),
        "prompt_tok_s": round(in_tokens / ttft, 1) if in_tokens else None,
        "decode_tok_s": round(max(out_tokens - 1, 0) / decode_s, 1),
        "total_s": round(end - t0, 2),
        "answer": "".join(text)[:200],
    }


def filler(words: int) -> str:
    text = FILLER * (words // len(FILLER.split()) + 1)
    return " ".join(text.split()[:words])


def cases(long: bool) -> list[tuple[str, str, int]]:
    out = [
        ("short", "Name three uses for a paperclip. Answer in one sentence.", 128),
        ("story-2k", "Write a long, detailed short story about a lighthouse keeper.", 2048),
        ("prompt-4k", filler(3000) + "\n\nSummarise the text above in one sentence.", 64),
    ]
    if long:
        out.append(("prompt-32k", filler(24000) + "\n\nSummarise the text above in one sentence.", 64))
    return out


TOKENS_PER_WORD = 1.21  # the filler, measured in Phase 0: 24,000 words -> 29,029 tokens
NEEDLE = "7391-ALPHA"


def needle_cases(tokens: int, think: bool = False) -> list[tuple[str, str, int, str]]:
    """(name, prompt, max_tokens, expected) with the code at 10%, 50% and 90% depth."""
    words = int(tokens * 0.9 / TOKENS_PER_WORD)
    fact = f"Note for later: the vault code is {NEEDLE}."
    question = "\n\nWhat is the vault code given in the text above? Answer with the code only."
    out = []
    for pct in (10, 50, 90):
        cut = words * pct // 100
        prompt = " ".join([filler(cut), fact, filler(words - cut)]) + question
        name = f"needle-{tokens // 1000}k-{pct}" + ("-think" if think else "")
        out.append((name, prompt, 8192 if think else 64, NEEDLE))
    return out


def needle_found(answer: str, expected: str) -> bool:
    return "".join(expected.lower().split()) in "".join(answer.lower().split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--base", default="http://127.0.0.1:8086")
    ap.add_argument("--out", default="results")
    ap.add_argument("--long", action="store_true")
    ap.add_argument("--needle", type=int, metavar="TOKENS")
    ap.add_argument("--think", action="store_true")
    a = ap.parse_args()
    runs: list[tuple[str, str, int, str | None]] = (
        list(needle_cases(a.needle, a.think)) if a.needle else [(*c, None) for c in cases(a.long)]
    )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"bench-{a.label}.jsonl").open("a") as f:
        for name, prompt, max_tokens, expect in runs:
            s = Sampler()
            s.start()
            try:
                row: dict[str, Any] = {
                    "label": a.label,
                    "case": name,
                    **stream(a.base, prompt, max_tokens, think=expect is None or a.think),
                }
                if expect is not None:
                    row["needle_found"] = needle_found(row["answer"], expect)
            except Exception as e:  # noqa: BLE001 - a failed case is a result too
                row = {"label": a.label, "case": name, "error": repr(e)}
            row.update(s.result())
            line = json.dumps(row)
            print(line, flush=True)
            f.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
