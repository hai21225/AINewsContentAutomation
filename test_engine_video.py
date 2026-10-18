import subprocess
from pathlib import Path


class WhiteboardService:
    def __init__(self):
        self.engine_dir = (
            Path(__file__).resolve().parents[1]
            / "Lib"
            / "WhiteBoardEngine"
            / "whiteboard-video-engine"
        )

        self.cli_path = (
            self.engine_dir
            / ".venv"
            / "Scripts"
            / "whiteboard.exe"
        )

    def render_image(
        self,
        image_path: str,
        output_path: str,
        duration: int = 5,
        fps: int = 24,
    ):
        image_path = Path(image_path).resolve()
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.cli_path.exists():
            raise FileNotFoundError(
                f"Không tìm thấy WhiteBoardEngine CLI: {self.cli_path}"
            )

        if not image_path.exists():
            raise FileNotFoundError(
                f"Không tìm thấy ảnh đầu vào: {image_path}"
            )

        command = [
            str(self.cli_path),
            "render-image",
            str(image_path),
            "-o",
            str(output_path),
            "--duration",
            str(duration),
            "--fps",
            str(fps),
        ]

        result = subprocess.run(
            command,
            cwd=str(self.engine_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"WhiteBoardEngine render thất bại:\n"
                f"{result.stderr}\n{result.stdout}"
            )

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(
                f"Engine không tạo được video hợp lệ: {output_path}"
            )

        return output_path