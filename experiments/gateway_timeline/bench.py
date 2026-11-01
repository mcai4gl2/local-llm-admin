"""Bounded paired gateway workload for an approved off/on timeline rollout."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

SCHEDULE = {"serial": 12, "concurrent_pairs": 6, "cancelled": 4}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) *
                 (position - lower), 4)


def summarize(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for case in ("serial", "concurrent", "cancelled"):
        selected = [sample for sample in samples if sample["case"] == case]
        completed = [sample for sample in selected if sample["status"] == 200 and
                     not sample["cancelled"] and sample.get("error_type") is None]
        metrics: dict[str, Any] = {"count": len(selected), "completed": len(completed),
                                   "failed": sum(s["status"] != 200 or
                                                 s.get("error_type") is not None for s in selected)}
        if case != "cancelled":
            for key in ("first_event_s", "first_content_s", "total_s"):
                values = [float(s[key]) for s in completed if s.get(key) is not None]
                metrics[key.removesuffix("_s") + "_measured"] = len(values)
                metrics[key.removesuffix("_s") + "_p50_s"] = (
                    round(statistics.median(values), 4) if values else None
                )
                metrics[key.removesuffix("_s") + "_p95_s"] = percentile(values, 0.95)
        result[case] = metrics
    return result


def compare(off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    if off.get("phase") != "off" or on.get("phase") != "on":
        raise ValueError("expected an off phase and an on phase")
    if off.get("payload_sha256") != on.get("payload_sha256"):
        raise ValueError("payload changed between phases")
    if off.get("schedule") != on.get("schedule"):
        raise ValueError("schedule changed between phases")
    schedule = off["schedule"]
    expected = {"serial": schedule["serial"],
                "concurrent": 2 * schedule["concurrent_pairs"],
                "cancelled": schedule["cancelled"]}
    for phase in (off, on):
        counts = {case: sum(s["case"] == case for s in phase["samples"])
                  for case in expected}
        if counts != expected:
            raise ValueError("incomplete phase schedule")
    return {"off": summarize(off["samples"]), "on": summarize(on["samples"]),
            "limitations": (
                "Observed client latency before and after deployment. Engine load, cache, "
                "restart state and external traffic can differ; these values alone do not "
                "measure tracing overhead or native execution time."
            )}


async def _one(client: httpx.AsyncClient, url: str, key: str, payload: dict[str, Any],
               case: str, *, cancel: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    sample: dict[str, Any] = {"case": case, "status": None, "first_event_s": None,
                              "first_content_s": None, "total_s": None,
                              "cancelled": cancel, "request_id": None}
    try:
        async with client.stream("POST", url, headers={"Authorization": "Bearer " + key},
                                 json=payload) as response:
            sample["status"] = response.status_code
            sample["request_id"] = response.headers.get("x-request-id")
            if response.status_code == 200:
                async for line in response.aiter_lines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    try:
                        event = json.loads(line[5:])
                    except ValueError:
                        sample["error_type"] = "MalformedEvent"
                        break
                    if not isinstance(event, dict):
                        continue
                    elapsed = time.perf_counter() - started
                    if sample["first_event_s"] is None:
                        sample["first_event_s"] = elapsed
                    choices = event.get("choices")
                    if isinstance(choices, list):
                        for choice in choices:
                            delta = choice.get("delta") if isinstance(choice, dict) else None
                            content = delta.get("content") if isinstance(delta, dict) else None
                            if isinstance(content, str) and content and sample["first_content_s"] is None:
                                sample["first_content_s"] = elapsed
                    if "error" in event:
                        sample["error_type"] = "UpstreamError"
                    if cancel:
                        break
    except (httpx.HTTPError, TimeoutError) as exc:
        sample["error_type"] = type(exc).__name__
    sample["total_s"] = time.perf_counter() - started
    return sample


async def run(phase: str, out: Path) -> dict[str, Any]:
    base = os.environ["GATEWAY_BASE_URL"].rstrip("/")
    key = os.environ["INFERENCE_API_KEY"]
    model = os.environ.get("INFERENCE_MODEL_ID", "local-model")
    payload = {"model": model, "messages": [{"role": "user",
               "content": "Reply with one short sentence about the weather."}],
               "max_tokens": 32, "stream": True}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    url = base + "/chat/completions"
    samples: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0),
                                 trust_env=False) as client:
        for _ in range(2):
            await _one(client, url, key, payload, "warmup")
        for _ in range(SCHEDULE["serial"]):
            samples.append(await _one(client, url, key, payload, "serial"))
        for _ in range(SCHEDULE["concurrent_pairs"]):
            samples.extend(await asyncio.gather(
                _one(client, url, key, payload, "concurrent"),
                _one(client, url, key, payload, "concurrent"),
            ))
        for _ in range(SCHEDULE["cancelled"]):
            samples.append(await _one(client, url, key, payload, "cancelled", cancel=True))
    result = {"phase": phase, "payload_sha256": digest, "schedule": SCHEDULE,
              "samples": samples}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--phase", required=True, choices=("off", "on"))
    run_parser.add_argument("--out", required=True, type=Path)
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("off", type=Path)
    compare_parser.add_argument("on", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        result = asyncio.run(run(args.phase, args.out))
        print(json.dumps({"phase": args.phase, "summary": summarize(result["samples"])}))
    else:
        print(json.dumps(compare(json.loads(args.off.read_text()),
                                 json.loads(args.on.read_text())), indent=2))


if __name__ == "__main__":
    main()
