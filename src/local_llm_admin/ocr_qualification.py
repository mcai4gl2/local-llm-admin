"""Offline OCR accuracy/load scoring against references frozen before candidate inference."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any


def normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def distance(expected: str, actual: str) -> int:
    row = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        next_row = [i]
        for j, right in enumerate(actual, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
        row = next_row
    return row[-1]


def evaluate(reference: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    pages = result.get("pages", [])
    expected = reference["pages"]
    complete = (result.get("complete") is True and result.get("truncated") is False
                and len(pages) == len(expected)
                and [p.get("number") for p in pages] == list(range(1, len(expected) + 1)))
    errors = sum(distance(normalized(text), normalized(str(page.get("text", ""))))
                 for text, page in zip(expected, pages, strict=False))
    missing = sum(len(normalized(text)) for text in expected[len(pages):])
    extra = sum(len(normalized(str(page.get("text", "")))) for page in pages[len(expected):])
    count = sum(len(normalized(text)) for text in expected)
    cer = (errors + missing + extra) / max(1, count)
    output = "\n".join(str(page.get("text", "")) for page in pages)
    numeric = all(re.search(r"(?<![\d.])" + re.escape(field) + r"(?![\d.])", output)
                  for field in reference.get("numeric_fields", []))
    markdown = "\n".join(str(page.get("markdown", "")) for page in pages)
    table = reference.get("table")
    table_ok = True
    if table:
        # Require separate rows and separate cells, not merely all cell words somewhere.
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", markdown, flags=re.I | re.S)
        cells = [[normalized(re.sub(r"<[^>]+>", "", cell)) for cell in
                  re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.I | re.S)] for row in rows]
        if not cells:
            cells = [[normalized(cell) for cell in line.strip().strip("|").split("|")]
                     for line in markdown.splitlines() if "|" in line and not re.fullmatch(r"[\s|:\-]+", line)]
        table_ok = cells == [[normalized(cell) for cell in row] for row in table]
    return {"passed": complete and cer <= reference.get("max_cer", 0.02) and numeric and table_ok,
            "complete": complete, "cer": cer, "numeric_exact": numeric, "table_structure": table_ok}


def compare_load(baseline: dict[str, Any], combined: dict[str, Any]) -> dict[str, Any]:
    keys = ("chat_p95_ms", "chat_tokens_s", "embed_p95_ms")
    valid = all(type(sample.get(key)) in (int, float) and math.isfinite(sample[key]) and sample[key] > 0
                for sample in (baseline, combined) for key in keys)
    valid = valid and all(type(sample.get(key)) is int and sample[key] >= 30
                          for sample in (baseline, combined) for key in ("chat_requests", "embed_requests"))
    if not valid or any(sample.get("errors") != 0 for sample in (baseline, combined)):
        return {"passed": False, "reason": "Missing/invalid baseline or request failures."}
    ratios = {key: combined[key] / baseline[key] for key in keys}
    return {"passed": ratios["chat_p95_ms"] <= 1.25 and ratios["embed_p95_ms"] <= 1.25
            and ratios["chat_tokens_s"] >= 0.9, "ratios": ratios}


def compare_memory(sample: dict[str, Any]) -> dict[str, Any]:
    keys = ("ocr_peak_gb", "reservation_gb", "aggregate_peak_gb", "ram_gb", "wired_limit_gb", "headroom_gb")
    if (sample.get("pressure_normal") is not True or not all(
        type(sample.get(key)) in (int, float) and math.isfinite(sample[key]) and sample[key] > 0 for key in keys
    )):
        return {"passed": False, "reason": "Missing native memory evidence or non-normal pressure."}
    return {"passed": sample["ocr_peak_gb"] <= sample["reservation_gb"]
            and sample["aggregate_peak_gb"] + sample["headroom_gb"] <= sample["ram_gb"]
            and sample["aggregate_peak_gb"] <= sample["wired_limit_gb"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--combined", type=Path)
    parser.add_argument("--memory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    references = json.loads(args.references.read_text())
    results = json.loads(args.results.read_text())
    scores = {name: evaluate(ref, results[name]) if name in results else
              {"passed": False, "reason": "No candidate result."} for name, ref in references["cases"].items()}
    load = (compare_load(json.loads(args.baseline.read_text()), json.loads(args.combined.read_text()))
            if args.baseline and args.combined else {"passed": False, "reason": "Load evidence pending."})
    memory = (compare_memory(json.loads(args.memory.read_text())) if args.memory else
              {"passed": False, "reason": "Native memory/pressure evidence pending."})
    report = {"schema_version": 1, "tasks": scores, "load": load, "memory": memory,
              "passed": all(score["passed"] for score in scores.values()) and load["passed"] and memory["passed"]}
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
