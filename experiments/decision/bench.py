"""Phase 0 bench for the OpenJev MLX sidecar. Stdlib only; runs on the Mac.

usage: phase0_bench.py <label> [--qwen-load] [--fixtures DIR] [--useful]
Prints one JSON summary line per test.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("DJ_BASE", "http://127.0.0.1:8081")
KEY = os.environ.get("OPENJEV_API_KEY", "")
QWEN = "http://127.0.0.1:8080/v1/chat/completions"

STATE = {
    "ticket": "My card was charged twice for the October invoice and the app now shows "
    "a sync error when I open billing settings. Please refund the duplicate charge.",
    "customer_tier": "pro",
    "previous_tickets": 2,
}
Q_ALL: dict[str, Any] = {
    "is_billing": {"type": "noul", "instructions": "Is this mainly a billing issue?"},
    "department": {
        "type": "choice",
        "criteria": {
            "billing": "Payments, invoices, refunds",
            "technical": "Bugs, errors, crashes",
            "account": "Login, profile, permissions",
            "other": "Anything else",
        },
    },
    "urgency": {"type": "score", "criteria": ["low", "normal", "high", "critical"]},
}


def questions(n: int) -> dict[str, Any]:
    base = list(Q_ALL.items())
    out = {}
    for i in range(n):
        k, v = base[i % len(base)]
        out[f"{k}_{i}" if i >= len(base) else k] = v
    return out


def post(path: str, body: dict[str, Any], url: str | None = None, timeout: float = 600) -> tuple[int, dict[str, Any], dict[str, str], float]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url or BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if KEY and url is None:
        req.add_header("Authorization", f"Bearer {KEY}")
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), dict(r.headers), time.perf_counter() - t
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers), time.perf_counter() - t


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def pid_of_sidecar() -> int | None:
    try:
        return int(open("/Volumes/Models/decision/run/openjev.pid").read().strip())
    except (OSError, ValueError):
        return None


def rss_mb(pid: int | None) -> float | None:
    if not pid:
        return None
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) / 1024 if out else None


def pressure() -> int:
    out = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True).stdout
    return int(out.strip() or 0)


def swap() -> str:
    return subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout.strip()


class Sampler(threading.Thread):
    def __init__(self, pids: dict[str, int | None]) -> None:
        super().__init__(daemon=True)
        self.pids, self.stop, self.peak = pids, threading.Event(), {k: 0.0 for k in pids}
        self.max_pressure = 0

    def run(self) -> None:
        while not self.stop.is_set():
            for k, p in self.pids.items():
                self.peak[k] = max(self.peak[k], rss_mb(p) or 0.0)
            self.max_pressure = max(self.max_pressure, pressure())
            time.sleep(0.5)


def bench(label: str, n_q: int, reps: int, samples: int | None) -> dict[str, Any]:
    lat, model_ms, toks, errors = [], [], [], 0
    body: dict[str, Any] = {"state": STATE, "model": "openjev-latest", "questions": questions(n_q)}
    if samples is not None:
        body["samples"] = samples
    for r in range(reps):
        if os.environ.get("DJ_UNIQUE") == "1":  # defeat the prefill cache: a new state each time
            body["state"] = dict(STATE, ticket=f"[case {time.time_ns()}-{r}] " + STATE["ticket"])
        code, resp, hdr, dt = post("/v1/systemone", body)
        if code != 200:
            errors += 1
            continue
        lat.append(dt * 1000)
        st = hdr.get("server-timing") or hdr.get("Server-Timing") or ""
        for part in st.split(","):
            if part.strip().startswith("model;dur="):
                model_ms.append(float(part.split("=")[1]))
        toks.append(resp.get("usage", {}).get("input_tokens"))
    return {
        "test": label, "questions": n_q, "samples": samples, "n": len(lat), "errors": errors,
        "p50_ms": round(statistics.median(lat), 1) if lat else None,
        "p95_ms": round(pct(lat, 0.95), 1) if lat else None,
        "model_p50_ms": round(statistics.median(model_ms), 1) if model_ms else None,
        "input_tokens": toks[0] if toks else None,
    }


def qwen_long(prompt_tokens: int, max_tokens: int, out: dict[str, Any]) -> None:
    filler = "The quick brown fox jumps over the lazy dog. " * (prompt_tokens // 10)
    body = {
        "model": "/Volumes/Models/models/active",
        "messages": [{"role": "user", "content": filler + "\nSummarise the text above in one sentence."}],
        "max_tokens": max_tokens, "temperature": 0, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    code, resp, _h, dt = post("", body, url=QWEN, timeout=1200)
    u = resp.get("usage", {}) if code == 200 else {}
    out.update({"qwen_status": code, "qwen_wall_s": round(dt, 1), "qwen_usage": u})


def qwen_decode_rate(max_tokens: int = 256) -> dict[str, Any]:
    body = {
        "model": "/Volumes/Models/models/active",
        "messages": [{"role": "user", "content": "Write a 300-word story about a lighthouse keeper."}],
        "max_tokens": max_tokens, "temperature": 0, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    code, resp, _h, dt = post("", body, url=QWEN, timeout=600)
    u = resp.get("usage", {}) if code == 200 else {}
    ct = u.get("completion_tokens") or 0
    return {"qwen_status": code, "completion_tokens": ct, "wall_s": round(dt, 2), "tok_per_s_incl_prefill": round(ct / dt, 1) if dt else None}


def fixtures(d: str) -> None:
    os.makedirs(d, exist_ok=True)
    cases = {
        "noul": {"state": STATE, "model": "openjev-latest", "questions": {"is_billing": Q_ALL["is_billing"]}},
        "choice": {"state": STATE, "model": "openjev-latest", "questions": {"department": Q_ALL["department"]}},
        "score": {"state": STATE, "model": "openjev-latest", "questions": {"urgency": Q_ALL["urgency"]}},
        "error_400_choice_empty": {"state": STATE, "model": "openjev-latest", "questions": {"x": {"type": "choice", "criteria": {}}}},
        "error_400_bad_type": {"state": STATE, "model": "openjev-latest", "questions": {"x": {"type": "maybe"}}},
        "error_400_unknown_model": {"state": STATE, "model": "nope", "questions": {"is_billing": Q_ALL["is_billing"]}},
    }
    for name, body in cases.items():
        code, resp, hdr, _dt = post("/v1/systemone", body)
        keep = {k: v for k, v in hdr.items() if k.lower() in ("server-timing", "content-type", "x-request-id")}
        with open(os.path.join(d, f"{name}.json"), "w") as f:
            json.dump({"request": body, "status": code, "headers": keep, "response": resp}, f, indent=2)
        print(json.dumps({"fixture": name, "status": code}))


USEFUL = [
    # (prompt, expected route) — a realistic homelab router: which backend should take this?
    ("Refactor this Python function to use a dataclass and add type hints.", "code"),
    ("Why does my Rust borrow checker complain about a mutable reference in a loop?", "code"),
    ("Write a bash one-liner to find the 10 largest files under /var.", "code"),
    ("Fix the failing pytest: AssertionError expected 3 got 4 in test_parse.", "code"),
    ("Explain the SQL join below and make it faster with an index.", "code"),
    ("Generate an image of a red fox in snow at sunset, watercolour style.", "image"),
    ("Make me a 1024x1024 logo of a coffee cup with steam, flat design.", "image"),
    ("Draw a cyberpunk city street at night with neon signs.", "image"),
    ("Create a photo-realistic picture of a golden retriever on a beach.", "image"),
    ("Render a fantasy castle on a cliff, digital painting.", "image"),
    ("What is the integral of x^2 * e^x dx?", "math"),
    ("Solve for x: 3x^2 - 12x + 9 = 0.", "math"),
    ("A train leaves at 3pm going 80 km/h; when does it cover 200 km?", "math"),
    ("Prove that the square root of 2 is irrational.", "math"),
    ("What is the probability of rolling two sixes with two dice?", "math"),
    ("Hi! How was your weekend? Any good movie recommendations?", "chat"),
    ("Can you help me write a polite email declining a meeting invite?", "chat"),
    ("Summarise the plot of Pride and Prejudice in three sentences.", "chat"),
    ("What are some tips for sleeping better when travelling?", "chat"),
    ("Translate 'good morning, how are you' into French and Japanese.", "chat"),
]


def useful() -> None:
    q = {"route": {"type": "choice", "instructions": "Which backend should handle this user request?",
                   "criteria": {"code": "Programming, debugging, shell, SQL",
                                "image": "Generate or draw an image",
                                "math": "Calculations, equations, proofs, probability",
                                "chat": "General conversation, writing, translation, advice"}}}
    right, rows = 0, []
    for prompt, want in USEFUL:
        code, resp, _h, dt = post("/v1/systemone", {"state": {"request": prompt}, "model": "openjev-latest", "questions": q})
        a = resp.get("answers", {}).get("route", {}) if code == 200 else {}
        got, conf = a.get("choice"), a.get("confidence")
        right += got == want
        rows.append({"want": want, "got": got, "conf": conf, "ms": round(dt * 1000)})
    ok = [r["conf"] for r in rows if r["got"] == r["want"] and r["conf"] is not None]
    bad = [r["conf"] for r in rows if r["got"] != r["want"] and r["conf"] is not None]
    print(json.dumps({"test": "useful-router", "accuracy": f"{right}/{len(USEFUL)}",
                      "conf_right_mean": round(statistics.mean(ok), 3) if ok else None,
                      "conf_wrong_mean": round(statistics.mean(bad), 3) if bad else None,
                      "wrong": [r for r in rows if r["got"] != r["want"]],
                      "ms_p50": statistics.median(r["ms"] for r in rows)}))


def main() -> None:
    label = sys.argv[1]
    args = sys.argv[2:]
    if "--fixtures" in args:
        fixtures(args[args.index("--fixtures") + 1])
        return
    if "--useful" in args:
        useful()
        return
    qwen_pid = None
    try:
        qwen_pid = int(open("/Volumes/Models/run/mlx-server.pid").read().strip())
    except (OSError, ValueError):
        pass
    s = Sampler({"sidecar": pid_of_sidecar(), "qwen": qwen_pid})
    s.start()
    print(json.dumps({"label": label, "pressure_start": pressure(), "swap_start": swap(),
                      "rss_mb": {"sidecar": rss_mb(pid_of_sidecar()), "qwen": rss_mb(qwen_pid)}}))
    qout: dict[str, Any] = {}
    th = None
    if "--qwen-rate" in args:
        print(json.dumps({"test": "qwen-decode", **qwen_decode_rate()}))
    if "--qwen-load" in args:
        th = threading.Thread(target=qwen_long, args=(30000, 200, qout))
        th.start()
        time.sleep(2)
    post("/v1/systemone", {"state": STATE, "model": "openjev-latest", "questions": questions(1)})  # warm
    for n_q in (1, 3, 10):
        print(json.dumps(bench(label, n_q, 20, 1)))
    print(json.dumps(bench(label + "-auto", 3, 20, None)))
    if th:
        th.join()
        print(json.dumps({"test": "qwen-long-concurrent", **qout}))
    s.stop.set()
    s.join()
    print(json.dumps({"label": label, "peak_rss_mb": {k: round(v) for k, v in s.peak.items()},
                      "max_pressure": s.max_pressure, "swap_end": swap()}))


if __name__ == "__main__":
    main()
