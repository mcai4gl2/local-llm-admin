"""Generate public OCR references with Pillow and a caller-supplied licensed CJK font."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def render(text: str, font: Any, *, columns: bool = False, table: bool = False) -> Any:
    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    if columns:
        for i, line in enumerate(text.splitlines()):
            draw.text((60 + (i // 2) * 580, 100 + (i % 2) * 80), line, font=font, fill="black")
    elif table:
        for i, line in enumerate(text.splitlines()):
            cells = line.split()
            for j, cell in enumerate(cells):
                draw.text((80 + j * 400, 120 + i * 100), cell, font=font, fill="black")
        for y in (100, 200, 300, 400):
            draw.line((60, y, 860, y), fill="black", width=3)
        for x in (60, 460, 860):
            draw.line((x, 100, x, 400), fill="black", width=3)
    else:
        draw.multiline_text((60, 100), text, font=font, fill="black", spacing=35)
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--font-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.font.read_bytes()).hexdigest() != args.font_sha256:
        raise ValueError("font hash differs")
    references = json.loads(Path(__file__).with_name("references.json").read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    font = ImageFont.truetype(str(args.font), 38)
    outputs = {}
    for name, case in references["cases"].items():
        pages = [render(text, font, columns=name == "columns", table=name == "table") for text in case["pages"]]
        if "rotation_degrees" in case:
            pages[0] = pages[0].rotate(case["rotation_degrees"], expand=True)
        path = args.output / (name + (".pdf" if len(pages) > 1 else ".png"))
        if len(pages) > 1:
            pages[0].save(path, "PDF", resolution=144, save_all=True, append_images=pages[1:])
        else:
            pages[0].save(path, "PNG", optimize=True)
        outputs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (args.output / "manifest.json").write_text(json.dumps({"font_sha256": args.font_sha256, "files": outputs}, indent=2) + "\n")


if __name__ == "__main__":
    main()
