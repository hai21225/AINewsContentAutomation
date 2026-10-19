#!/usr/bin/env python3
"""Run Informative Drawings on one image and write the requested output path."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Informative Drawings single-image wrapper")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--style", default=os.getenv("WHITEBOARD_INFORMATIVE_STYLE", "anime_style"))
    parser.add_argument("--size", type=int, default=int(os.getenv("WHITEBOARD_INFORMATIVE_SIZE", "768")))
    args = parser.parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()

    repo = Path(__file__).resolve().parents[1] / "informative-drawings"
    checkpoint = _find_checkpoint(repo, args.style)
    if not checkpoint.exists():
        raise SystemExit(
            "Informative Drawings weight missing. Expected netG_A_latest.pth under "
            f"{repo / 'checkpoints' / args.style} or {repo / 'checkpoints' / 'model' / args.style}"
        )

    sys.path.insert(0, str(repo))
    os.chdir(repo)

    import torch
    import torchvision.transforms as transforms
    from model import Generator
    from PIL import Image

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    source = Image.open(input_path).convert("RGB")
    original_size = source.size

    transform_steps = []
    if args.size > 0:
        transform_steps.append(transforms.Resize(int(args.size), Image.BICUBIC))
    transform_steps.append(transforms.ToTensor())
    tensor = transforms.Compose(transform_steps)(source).unsqueeze(0).to(device)

    model = Generator(3, 1, 3).to(device)
    state = torch.load(checkpoint, map_location=device)
    model.load_state_dict(state)
    model.eval()

    with torch.no_grad():
        prediction = model(tensor)[0, 0].detach().cpu().clamp(0, 1).numpy()

    output = Image.fromarray((prediction * 255).astype("uint8"), mode="L")
    if output.size != original_size:
        output = output.resize(original_size, Image.Resampling.BICUBIC)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.convert("RGB").save(output_path)
    return 0


def _find_checkpoint(repo: Path, style: str) -> Path:
    candidates = [
        repo / "checkpoints" / style / "netG_A_latest.pth",
        repo / "checkpoints" / "model" / style / "netG_A_latest.pth",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


if __name__ == "__main__":
    raise SystemExit(main())
