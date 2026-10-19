#!/usr/bin/env python3
"""Run Anime2Sketch on one image and write the exact requested output path."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Anime2Sketch single-image wrapper")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", choices=["auto", "default", "improved"], default="auto")
    parser.add_argument("--load-size", type=int, default=int(os.getenv("WHITEBOARD_ANIME2SKETCH_SIZE", "768")))
    args = parser.parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()

    repo = Path(__file__).resolve().parents[1] / "Anime2Sketch"
    weights = repo / "weights"
    model_name = args.model
    if model_name == "auto":
        model_name = "improved" if (weights / "improved.bin").exists() else "default"
    expected = weights / ("improved.bin" if model_name == "improved" else "netG.pth")
    if not expected.exists():
        raise SystemExit(f"Anime2Sketch weight missing: {expected}")

    sys.path.insert(0, str(repo))
    os.chdir(repo)

    import torch
    from data import read_img_path, save_image, tensor_to_img
    from model import create_model

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    original_load = torch.load

    def load_on_device(*load_args, **load_kwargs):
        load_kwargs.setdefault("map_location", device)
        return original_load(*load_args, **load_kwargs)

    torch.load = load_on_device

    model = create_model(model_name).to(device)
    model.eval()
    image, original_size = read_img_path(str(input_path), args.load_size)
    with torch.no_grad():
        output = model(image.to(device))
    output_image = tensor_to_img(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_image(output_image, str(output_path), original_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
