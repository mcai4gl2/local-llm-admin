"""Bounded, deterministic synthetic retrieval inputs; standard library only."""

from __future__ import annotations

import hashlib

POSITIONS = (10, 50, 90)
SEEDS = (0, 1)


def expected_value(position: int, seed: int) -> str:
    if (
        type(position) is not int
        or position not in POSITIONS
        or type(seed) is not int
        or seed not in SEEDS
    ):
        raise ValueError("Invalid synthetic retrieval position or seed.")
    return "result-" + hashlib.sha256(f"context-1:{position}:{seed}".encode()).hexdigest()[:16]


def make_messages(position: int, seed: int, records: int) -> list[dict[str, str]]:
    value = expected_value(position, seed)
    if type(records) is not int or not 64 <= records <= 4096:
        raise ValueError("Synthetic record count outside the fixed bounds.")
    words = ("amber", "birch", "cedar", "dune", "elm", "fern", "grove", "hazel")
    lines = []
    for index in range(records):
        if index == records * position // 100:
            lines.append(f"Entry needle-{position}-{seed} stores {value}.")
        else:
            items = " ".join(words[(index + seed + offset) % len(words)] for offset in range(6))
            lines.append(f"Entry ref-{index:06d} stores {items}.")
    text = (
        "Read the records below. Each entry has one key and one value. Retrieve the exact value requested at the end.\n<records>\n"
        + "\n".join(lines)
        + f'\n</records>\nWhat value does entry needle-{position}-{seed} store? Return only the JSON object {{"value":"the exact stored value"}}. Do not add explanation.'
    )
    return [{"role": "user", "content": text}]
