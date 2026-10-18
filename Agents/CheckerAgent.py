from Agents.BaseAgent import BaseAgent
from Errors import AgentOutputError

CHECKER_OUTPUT_FORMAT = {
    "type": "object",
    "properties": {
        "passed": {
            "type": "boolean"
        },
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 10
        },
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string"
                    },
                    "message": {
                        "type": "string"
                    }
                },
                "required": ["type", "message"]
            }
        },
        "revision_instruction": {
            "type": "string"
        }
    },
    "required": [
        "passed",
        "score",
        "issues",
        "revision_instruction"
    ]
}

MIN_SCORE = 0.0
MAX_SCORE = 10.0


class CheckerAgent(BaseAgent):
    """Kiểm tra nội dung writer tạo so với bài báo nguồn và chấm điểm.

    ``score`` là trường bắt buộc để xếp hạng ứng viên: nếu model trả về
    thiếu/không phải số, agent coi output là không dùng được
    (``AgentOutputError``). Các trường khác được chuẩn hoá mềm.
    """

    default_minimum_score = 8.0

    def __init__(self, config, llm_client, log_service=None):
        super().__init__(
            llm_client,
            config["role"],
            log_service=log_service,
            json_max_attempts=config.get("json_max_attempts"),
        )

        self._checker_prompt = config["prompt"]
        self._minimum_score = float(
            config.get("minimum_score", self.default_minimum_score)
        )

    @property
    def minimum_score(self):
        """Ngưỡng điểm tối thiểu để coi nội dung đạt yêu cầu."""
        return self._minimum_score

    def run(self, article, writer_output):
        task = f"""
            {self._checker_prompt}

            BÀI BÁO NGUỒN:

            Tiêu đề:
            {article.get("title", "")}

            Mô tả:
            {article.get("des", article.get("description", ""))}

            Nội dung:
            {article.get("content", "")}


            NỘI DUNG WRITER ĐÃ TẠO:

            {writer_output.get("tts_text", "")}

            HASHTAGS:
            {writer_output.get("hashtags", [])}


            Hãy kiểm tra:
            - Có thông tin nào không tồn tại trong nguồn không.
            - Có thông tin nào bị diễn giải sai không.
            - Tên riêng, số liệu, thời gian có chính xác không.
            - Có bỏ sót ý quan trọng khiến nội dung sai lệch không.
            - Nội dung có tự nhiên và phù hợp tin tức ngắn không.

            Nếu có lỗi, hãy chỉ rõ lỗi nằm ở đâu và cần sửa như thế nào.

            Hãy chấm điểm nội dung từ 0 đến 10 theo các tiêu chí sau:
            - Độ chính xác dữ kiện: 0 đến 4 điểm.
            - Không thêm thông tin ngoài nguồn: 0 đến 2 điểm.
            - Giữ đủ các ý quan trọng: 0 đến 2 điểm.
            - Tự nhiên và phù hợp khi đọc bằng TTS: 0 đến 1 điểm.
            - Độ dài phù hợp với bản tin ngắn: 0 đến 1 điểm.

            Trường passed chỉ được là true khi nội dung không có lỗi nghiêm trọng.
            revision_instruction phải nêu cụ thể cần sửa đoạn nào, sửa thành nội
            dung gì và phải dựa trên bài báo nguồn.
            """

        result = self.generate_json(
            task,
            CHECKER_OUTPUT_FORMAT,
            max_attempts=self._json_max_attempts,
        )

        return self.validate_result(result)

    def run_for_candidates(self, article, candidates):
        """Chấm điểm từng ứng viên, gắn kèm candidate gốc."""
        checked_candidates = []

        for candidate in candidates:
            check = self.run(article, candidate)
            check["candidate_id"] = candidate.get("candidate_id")
            check["candidate"] = candidate
            checked_candidates.append(check)

        return checked_candidates

    def validate_result(self, result):
        """Chuẩn hoá kết quả checker và kiểm tra trường bắt buộc."""
        if not isinstance(result, dict):
            raise AgentOutputError(
                "Checker phải trả về JSON object."
            )

        score = result.get("score")

        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise AgentOutputError(
                f"Checker trả về 'score' không hợp lệ: {score!r}."
            )

        score = min(MAX_SCORE, max(MIN_SCORE, float(score)))
        passed = self._coerce_passed(result.get("passed"))
        issues = result.get("issues")

        if not isinstance(issues, list):
            issues = []

        revision_instruction = result.get("revision_instruction")

        if not isinstance(revision_instruction, str):
            revision_instruction = ""

        return {
            "passed": passed,
            "score": score,
            "issues": issues,
            "revision_instruction": revision_instruction.strip(),
        }

    def _coerce_passed(self, value):
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)

        if isinstance(value, str):
            text = value.strip().lower()

            if text in ("true", "yes", "1"):
                return True

            if text in ("false", "no", "0", ""):
                return False

        self.log_warning(
            "checker_passed_invalid",
            f"Checker trả về 'passed' không hợp lệ ({value!r}); coi là false.",
        )
        return False
