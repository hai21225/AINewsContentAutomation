"""Offline line-art image generator."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PIL import Image, ImageDraw


class MockImageProvider:
    """Create deterministic black line-art PNGs for local pipeline tests."""

    def generate(self, prompt: str, out_path: Path, size: tuple[int, int]) -> Path:
        """Write a simple line-art image."""

        out_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = size
        image = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(image)
        seed = int(hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8], 16)
        margin_x = max(24, width // 10)
        margin_y = max(24, height // 10)
        cx, cy = width // 2, height // 2
        radius = min(width, height) // 5

        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(15, 15, 15), width=max(3, width // 210))
        draw.line((margin_x, cy + radius + margin_y // 3, width - margin_x, cy + radius + margin_y // 3), fill=(15, 15, 15), width=max(2, width // 260))
        for i in range(6):
            angle = (seed % 360 + i * 60) * math.pi / 180
            x1 = cx + math.cos(angle) * (radius + margin_x // 4)
            y1 = cy + math.sin(angle) * (radius + margin_y // 4)
            x2 = cx + math.cos(angle) * (radius + margin_x)
            y2 = cy + math.sin(angle) * (radius + margin_y)
            draw.line((x1, y1, x2, y2), fill=(15, 15, 15), width=max(2, width // 300))

        if seed % 2:
            draw.arc((margin_x, margin_y, width - margin_x, height - margin_y), start=205, end=335, fill=(15, 15, 15), width=max(3, width // 240))
            draw.line((width - margin_x * 1.5, cy, width - margin_x, cy + margin_y // 2), fill=(15, 15, 15), width=max(3, width // 240))
        else:
            draw.rectangle((margin_x * 1.3, margin_y * 1.4, width - margin_x * 1.3, height - margin_y * 1.4), outline=(15, 15, 15), width=max(3, width // 240))
            draw.line((margin_x * 1.3, height - margin_y * 1.4, cx, margin_y * 1.4), fill=(15, 15, 15), width=max(2, width // 300))
            draw.line((cx, margin_y * 1.4, width - margin_x * 1.3, height - margin_y * 1.4), fill=(15, 15, 15), width=max(2, width // 300))

        image.save(out_path)
        return out_path
