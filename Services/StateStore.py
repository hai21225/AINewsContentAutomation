"""Lưu trạng thái pipeline ra một file JSON để resume (Phase 3).

Nguyên tắc thiết kế:

- **Một file JSON duy nhất** (``Config/pipeline.json`` -> ``state.path``) chứa
  trạng thái của mọi bài trong job. Không dùng database: đọc cả file vào bộ
  nhớ, dựng index theo ``article_id``, tra cứu là O(1) — đủ cho quy mô vài bài
  mỗi lần chạy.
- File chỉ lưu **trạng thái + metadata + đường dẫn artifact**, KHÔNG lưu bytes
  của audio/ảnh/clip. Các file thật vẫn do ``TtsService``/``ImageService``/
  ``RenderService``/``MediaService`` tạo ở thư mục riêng.
- Lưu kèm **kết quả trung gian** (kịch bản từ bước ``write``, kế hoạch cảnh từ
  ``image_plan``) để khi chạy lại không phải gọi lại LLM.
- Ghi kiểu ``.part`` rồi ``replace`` (giống ``TtsService``) để không bao giờ có
  file dở dang.
- JSON hỏng/sai schema -> raise ``StateError``, **không tự xoá hay ghi đè** để
  không âm thầm mất trạng thái thật. Muốn bỏ state cũ thì dùng ``reset()``
  (tương ứng cờ ``--fresh`` ở CLI).

Hàm truy vấn dùng cho các bước sau (Phase 4/5) — tương đương ``SELECT`` theo
``article_id`` nhưng chạy trong bộ nhớ, ví dụ::

    store.get_article(article_id)
    store.is_stage_completed(article_id, "images")
    store.stage_artifacts(article_id, "images")   # chỉ trả path còn hợp lệ

Stage hợp lệ (khai báo sẵn cả ``render``/``mux``/``publish`` cho Phase 4/5)::

    write, tts, audio_duration, image_plan, images, render, mux, publish
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from Errors import StateError

SCHEMA_VERSION = 1
DEFAULT_STATE_PATH = "state/pipeline_state.json"

#: Toàn bộ bước có thể có trong pipeline. Phase 4/5 chỉ việc điền vào.
STAGES = (
    "write",
    "tts",
    "audio_duration",
    "image_plan",
    "images",
    "render",
    "mux",
    "publish",
)

PENDING = "pending"
COMPLETED = "completed"
FAILED = "failed"


def artifact_ok(path):
    """True nếu artifact tồn tại, là file và có kích thước > 0.

    State có thể "nói" một bước đã xong trong khi file đã bị xoá/hỏng; luôn
    kiểm tra lại trước khi dùng.
    """
    if not path:
        return False

    try:
        file_path = Path(path)
    except TypeError:  # pragma: no cover - path sai kiểu
        return False

    try:
        return file_path.is_file() and file_path.stat().st_size > 0
    except OSError:  # pragma: no cover - quyền/ổ đĩa
        return False


class StateStore:
    """Đọc/ghi và truy vấn trạng thái job trong một file JSON."""

    def __init__(self, path=None, log_service=None):
        self._path = Path(path or DEFAULT_STATE_PATH)
        self._log = log_service
        self._data = None

    @property
    def path(self):
        return self._path

    # ------------------------------------------------------------------
    # Vòng đời
    # ------------------------------------------------------------------

    def reset(self, job_id=None):
        """Bắt đầu state mới, bỏ toàn bộ trạng thái cũ (trong RAM)."""
        now = _now()
        self._data = {
            "schema_version": SCHEMA_VERSION,
            "created_at": now,
            "updated_at": now,
            "last_job_id": job_id,
            "selected_articles": [],
            "articles": {},
        }
        return self._data

    def load(self):
        """Nạp state từ đĩa (một lần, sau đó trả bản đã cache trong RAM)."""
        if self._data is not None:
            return self._data

        if not self._path.is_file():
            return self.reset()

        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as error:
            raise StateError(
                f"Không đọc được file trạng thái: {self._path}: {error}"
            ) from error

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise StateError(
                f"File trạng thái JSON không hợp lệ ({self._path}): {error}. "
                "Không tự ghi đè để tránh mất dữ liệu; dùng --fresh để bỏ."
            ) from error

        self._validate(data)
        self._data = data
        return self._data

    def save(self):
        """Ghi state ra đĩa theo kiểu nguyên tử (``.part`` rồi ``replace``)."""
        data = self.load()
        data["updated_at"] = _now()

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.parent / (self._path.name + ".part")

        try:
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self._path)
        except OSError as error:
            raise StateError(
                f"Không ghi được file trạng thái: {self._path}: {error}"
            ) from error

        return self._path

    def start_job(self, job_id=None):
        """Ghi nhận job id của lần chạy hiện tại."""
        data = self.load()
        data["last_job_id"] = job_id
        data["updated_at"] = _now()
        return data

    # ------------------------------------------------------------------
    # Bài viết
    # ------------------------------------------------------------------

    def upsert_article(self, article, index=None, total=None):
        """Thêm/cập nhật metadata của một bài; tạo sẵn mọi stage ``pending``."""
        data = self.load()
        article_id = article.get("id")

        if not article_id:
            raise StateError("Bài viết thiếu 'id', không lưu được trạng thái.")

        entry = data["articles"].get(article_id)

        if entry is None:
            entry = {
                "article_id": article_id,
                "title": article.get("title", ""),
                "source_url": article.get("source_url"),
                "category": article.get("category"),
                "index": index,
                "total": total,
                "status": PENDING,
                "failed_stage": None,
                "error": None,
                "stages": {stage: {"status": PENDING} for stage in STAGES},
            }
            data["articles"][article_id] = entry
            return entry

        entry["title"] = article.get("title", entry.get("title", ""))
        entry["source_url"] = article.get("source_url", entry.get("source_url"))
        entry["category"] = article.get("category", entry.get("category"))

        if index is not None:
            entry["index"] = index

        if total is not None:
            entry["total"] = total

        stages = entry.setdefault("stages", {})

        for stage in STAGES:
            stages.setdefault(stage, {"status": PENDING})

        return entry

    def set_selected_articles(self, article_ids):
        """Ghim danh sách bài đã chọn của lần chạy (để resume đúng tập bài)."""
        self.load()["selected_articles"] = list(article_ids)

    def get_article(self, article_id):
        return (self.load().get("articles") or {}).get(article_id)

    def iter_articles(self):
        return list((self.load().get("articles") or {}).values())

    def mark_article_status(self, article_id, status, failed_stage=None, error=None):
        entry = self._require_article(article_id)
        entry["status"] = status
        entry["failed_stage"] = failed_stage
        entry["error"] = error
        self.load()["updated_at"] = _now()
        return entry

    def is_article_completed(self, article_id):
        entry = self.get_article(article_id)
        return bool(entry and entry.get("status") == COMPLETED)

    # ------------------------------------------------------------------
    # Bước (stage)
    # ------------------------------------------------------------------

    def get_stage(self, article_id, stage):
        entry = self.get_article(article_id)

        if entry is None:
            return None

        return (entry.get("stages") or {}).get(stage)

    def is_stage_completed(self, article_id, stage):
        stage_state = self.get_stage(article_id, stage)
        return bool(stage_state and stage_state.get("status") == COMPLETED)

    def mark_stage_completed(self, article_id, stage, output=None,
                             artifacts=None, metadata=None):
        entry = self._require_article(article_id)
        stages = entry.setdefault("stages", {})
        previous = stages.get(stage) or {}

        stages[stage] = {
            "status": COMPLETED,
            "attempts": int(previous.get("attempts") or 0) + 1,
            "updated_at": _now(),
            "error": None,
            "output": output,
            "artifacts": [str(item) for item in (artifacts or [])],
            "metadata": metadata or {},
        }
        self.load()["updated_at"] = _now()
        return stages[stage]

    def mark_stage_failed(self, article_id, stage, error):
        entry = self._require_article(article_id)
        stages = entry.setdefault("stages", {})
        previous = stages.get(stage) or {}

        # Xoá output/artifact của lần chạy hỏng: không giữ lại kết quả dở dang
        # để lần resume sau chắc chắn chạy lại bước này.
        stages[stage] = {
            "status": FAILED,
            "attempts": int(previous.get("attempts") or 0),
            "updated_at": _now(),
            "error": str(error),
            "output": None,
            "artifacts": [],
            "metadata": {},
        }
        self.load()["updated_at"] = _now()
        return stages[stage]

    def stage_artifacts(self, article_id, stage, validate=True):
        """Đường dẫn artifact của bước, chỉ trả khi bước ``completed`` và file hợp lệ."""
        stage_state = self.get_stage(article_id, stage)

        if not stage_state or stage_state.get("status") != COMPLETED:
            return []

        paths = [str(item) for item in (stage_state.get("artifacts") or [])]

        if validate and not all(artifact_ok(path) for path in paths):
            return []

        return paths

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict):
            raise StateError("File trạng thái phải là một JSON object.")

        version = data.get("schema_version")

        if version != SCHEMA_VERSION:
            raise StateError(
                f"Phiên bản state không khớp: file={version!r}, "
                f"chương trình hỗ trợ={SCHEMA_VERSION}. "
                "Không tự ghi đè; dùng --fresh nếu muốn bắt đầu lại."
            )

        if not isinstance(data.get("articles"), dict):
            raise StateError("File trạng thái thiếu mục 'articles' hợp lệ.")

        data.setdefault("selected_articles", [])
        data.setdefault("last_job_id", None)

    def _require_article(self, article_id):
        entry = self.get_article(article_id)

        if entry is None:
            raise StateError(
                f"Chưa có trạng thái cho bài {article_id!r}; "
                "gọi upsert_article trước."
            )

        return entry


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
