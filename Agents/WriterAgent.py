from Agents.BaseAgent import BaseAgent
from Errors import ConfigurationError, ValidationError

WRITER_OUTPUT_FORMAT = {
    "type": "object",
    "properties": {
        "tts_text": {
            "type": "string"
        },
        "hashtags": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "minItems": 3,
            "maxItems": 5
        }
    },
    "required": [
        "tts_text",
        "hashtags"
    ]
}

MIN_HASHTAGS = 3
MAX_HASHTAGS = 5


class WriterAgent(BaseAgent):
    """Sinh nhiều ứng viên nội dung TTS và tự kiểm tra bằng code.

    Việc kiểm tra độ dài/hashtag là quy tắc xác định nên được làm trong code,
    không phó mặc cho LLM (theo yêu cầu của skill).
    """

    default_candidate_count = 3

    def __init__(self, config, llm_client, log_service=None):
        super().__init__(
            llm_client,
            config["role"],
            log_service=log_service,
            json_max_attempts=config.get("json_max_attempts"),
        )

        self._writer_prompt = config["prompt"]
        self._min_words = int(config["min_word"])
        self._max_words = int(config["max_word"])
        self._max_retries = int(config.get("max_retries", 1))
        self._candidate_count = int(
            config.get("candidate_count", self.default_candidate_count)
        )
        self._minimum_accepted_words = int(
            config.get("minimum_accepted_word", self._min_words)
        )
        self._maximum_accepted_words = int(
            config.get("maximum_accepted_word", self._max_words)
        )

        if self._min_words > self._max_words:
            raise ConfigurationError(
                "min_word phải nhỏ hơn hoặc bằng max_word."
            )

        if self._minimum_accepted_words > self._maximum_accepted_words:
            raise ConfigurationError(
                "minimum_accepted_word phải nhỏ hơn hoặc bằng "
                "maximum_accepted_word."
            )

        if self._candidate_count < 1:
            raise ConfigurationError(
                "candidate_count của writer phải lớn hơn hoặc bằng 1."
            )

    # ------------------------------------------------------------------
    # Thuộc tính công khai cho supervisor
    # ------------------------------------------------------------------

    @property
    def max_retries(self):
        """Số vòng writer tối đa supervisor được phép yêu cầu."""
        return self._max_retries

    @property
    def candidate_count(self):
        return self._candidate_count

    @staticmethod
    def count_words(text):
        return len(text.split())

    # ------------------------------------------------------------------
    # Sinh ứng viên
    # ------------------------------------------------------------------

    def run(self, article):
        title = str(article.get("title", ""))
        content = str(article.get("content", ""))
        description = article.get("des", article.get("description", ""))

        if not title.strip() or not content.strip():
            raise ValidationError(
                "Bài viết thiếu tiêu đề hoặc nội dung để viết kịch bản."
            )

        base_task = f"""
            {self._writer_prompt}

            YÊU CẦU BẮT BUỘC:
            - Mục tiêu tts_text từ {self._min_words} đến {self._max_words} từ
              (khoảng 1 phút đọc).
            - Chỉ sử dụng thông tin có trong bài báo nguồn.
            - Không tự thêm dữ kiện, suy đoán hoặc nhận xét.
            - Không đặt hashtag trong tts_text.
            - Hashtag chỉ được đặt trong trường hashtags, chuẩn hashtag lấy ra
              là dùng được phải đi kèm "#".
            - Hãy tận dụng các chi tiết, số liệu, nguyên nhân, kết quả và bối
              cảnh có trong bài báo để đạt đủ độ dài cũng như không vượt quá
              yêu cầu.

            BÀI BÁO NGUỒN:

            Tiêu đề:
            {title}

            Mô tả:
            {description}

            Nội dung:
            {content}
            """

        candidates = []
        rejected = []

        for attempt in range(self._candidate_count):
            payload = self.generate_json(
                base_task,
                WRITER_OUTPUT_FORMAT,
                max_attempts=self._json_max_attempts,
            )

            candidate = self.build_candidate(payload, title, attempt)

            if candidate["validation_errors"]:
                rejected.append(candidate)
                self.log_warning(
                    "writer_candidate_rejected",
                    f"Ứng viên {attempt + 1} bị loại.",
                    attempt=attempt + 1,
                    word_count=candidate["word_count"],
                    errors=candidate["validation_errors"],
                )
            else:
                candidates.append(candidate)
                self.log_info(
                    "writer_candidate_accepted",
                    f"Ứng viên {attempt + 1} hợp lệ.",
                    attempt=attempt + 1,
                    word_count=candidate["word_count"],
                )

        return {
            "candidates": candidates,
            "rejected_candidates": rejected,
            "article_title": title,
            "writer_attempt_count": self._candidate_count,
        }

    def revise(self, article, previous_result, feedback):
        """Sửa lại bản tốt nhất theo phản hồi của checker.

        Trả về một candidate đã chuẩn hoá (kể cả ``validation_errors``) để
        supervisor dùng thống nhất với ``run``.
        """
        feedback_text = self._feedback_text(feedback)

        if not feedback_text:
            raise ValidationError(
                "Thiếu nội dung phản hồi (revision_instruction) để sửa bài."
            )

        previous_text = ""
        candidate_id = None

        if isinstance(previous_result, dict):
            previous_text = str(previous_result.get("tts_text", ""))
            candidate_id = previous_result.get("candidate_id")

        title = str(article.get("title", ""))

        task = f"""
        {self._writer_prompt}

        BÀI BÁO NGUỒN:

        Tiêu đề:
        {title}

        Nội dung:
        {article.get("content", "")}

        BẢN TRƯỚC:

        {previous_text}

        PHẢN HỒI TỪ CHECKER:

        {feedback_text}

        Hãy sửa lại nội dung dựa trên phản hồi trên.

        Không được sửa những phần đang đúng nếu không cần thiết.
        Không được thêm thông tin ngoài bài báo nguồn.

        Mục tiêu tts_text từ {self._min_words} đến {self._max_words} từ.
        """

        payload = self.generate_json(
            task,
            WRITER_OUTPUT_FORMAT,
            max_attempts=self._json_max_attempts,
        )

        return self.build_candidate(payload, title, candidate_id)

    # ------------------------------------------------------------------
    # Chuẩn hoá & kiểm tra
    # ------------------------------------------------------------------

    def build_candidate(self, payload, title, candidate_id):
        """Chuẩn hoá payload của LLM thành candidate hoàn chỉnh."""
        raw_text = payload.get("tts_text")
        tts_text = raw_text.strip() if isinstance(raw_text, str) else ""

        candidate = {
            "tts_text": tts_text,
            "hashtags": self.normalize_hashtags(payload.get("hashtags")),
            "title": title,
            "candidate_id": candidate_id,
            "word_count": self.count_words(tts_text),
        }
        candidate["validation_errors"] = self.validate_candidate(candidate)

        return candidate

    def validate_candidate(self, candidate):
        """Trả về danh sách lỗi; rỗng nghĩa là ứng viên dùng được."""
        errors = []

        tts_text = candidate.get("tts_text") or ""

        if not tts_text.strip():
            errors.append("tts_text đang rỗng.")

        if "#" in tts_text:
            errors.append("tts_text không được chứa hashtag.")

        hashtags = candidate.get("hashtags")

        if not isinstance(hashtags, list) or len(hashtags) < MIN_HASHTAGS:
            errors.append(f"Phải có ít nhất {MIN_HASHTAGS} hashtag.")
        elif len(hashtags) > MAX_HASHTAGS:
            errors.append(f"Không được có quá {MAX_HASHTAGS} hashtag.")

        word_count = candidate.get("word_count")

        if not isinstance(word_count, int):
            word_count = self.count_words(tts_text)

        if word_count < self._minimum_accepted_words:
            errors.append(
                f"Nội dung quá ngắn: {word_count} từ, tối thiểu chấp nhận là "
                f"{self._minimum_accepted_words} từ."
            )

        if word_count > self._maximum_accepted_words:
            errors.append(
                f"Nội dung quá dài: {word_count} từ, tối đa chấp nhận là "
                f"{self._maximum_accepted_words} từ."
            )

        return errors

    @staticmethod
    def normalize_hashtags(raw_hashtags):
        """Chuẩn hoá hashtag: bỏ khoảng trắng, thêm '#' nếu thiếu."""
        if not isinstance(raw_hashtags, (list, tuple)):
            return []

        hashtags = []

        for hashtag in raw_hashtags:
            if not isinstance(hashtag, str):
                continue

            cleaned = hashtag.strip().replace(" ", "")

            if not cleaned or cleaned == "#":
                continue

            if not cleaned.startswith("#"):
                cleaned = "#" + cleaned

            hashtags.append(cleaned)

        return hashtags

    @staticmethod
    def _feedback_text(feedback):
        if isinstance(feedback, dict):
            return str(feedback.get("revision_instruction", "")).strip()

        if isinstance(feedback, str):
            return feedback.strip()

        return ""
