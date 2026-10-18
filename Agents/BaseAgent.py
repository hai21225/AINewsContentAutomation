import json

from Errors import AgentOutputError, ValidationError


class BaseAgent:
    """Lớp cơ sở cho mọi agent dùng LLM.

    Cung cấp:
    - ``build_prompt``: ghép vai trò + nhiệm vụ.
    - ``generate``: gọi LLM, trả về chuỗi thô.
    - ``generate_json``: gọi LLM và parse JSON có kiểm tra, tự thử lại có
      giới hạn khi model trả về JSON hỏng.
    """

    #: Số lần gọi LLM tối đa cho một lần cần JSON hợp lệ.
    default_json_max_attempts = 2

    def __init__(self, llm_client, role, log_service=None,
                 json_max_attempts=None):
        self._llm_client = llm_client
        self._role = role
        self._log = log_service

        if json_max_attempts is None:
            json_max_attempts = self.default_json_max_attempts

        self._json_max_attempts = max(1, int(json_max_attempts))

    def build_prompt(self, task):
        return f"""
            Vai trò của bạn:
            {self._role}

            Nhiệm vụ hiện tại:
            {task}

            Hãy thực hiện nhiệm vụ.
            """

    def generate(self, task, output_format=None):
        prompt = self.build_prompt(task)
        return self._llm_client.generate(prompt, output_format)

    def generate_json(self, task, output_format, max_attempts=None,
                      validator=None):
        """Gọi LLM và trả về JSON object đã parse.

        ``validator`` (nếu có) được gọi với object vừa parse và được phép
        chuẩn hoá tại chỗ. Nếu validator ném ``ValidationError`` (kể cả
        ``AgentOutputError``), lỗi đó được gửi lại cho model kèm yêu cầu sửa,
        trong cùng ngân sách ``max_attempts`` như lỗi JSON hỏng.

        Raises:
            ValidationError: khi mọi lần thử đều trả về output không dùng được.
        """
        attempts = max(1, int(max_attempts or self._json_max_attempts))
        current_task = task
        last_error = None

        for attempt in range(1, attempts + 1):
            raw = self.generate(current_task, output_format)

            try:
                data = self._parse_json_object(raw)

                if validator is not None:
                    validator(data)

                return data
            except ValidationError as error:
                last_error = error
                self.log_warning(
                    "agent_output_invalid",
                    attempt=attempt,
                    max_attempts=attempts,
                    error=str(error),
                )

                if attempt >= attempts:
                    break

                current_task = self._with_repair_instruction(task, error)

        raise last_error

    @staticmethod
    def _parse_json_object(raw):
        """Parse output của LLM thành dict, chịu được code fence/JSON lẫn văn bản."""
        if isinstance(raw, dict):
            return raw

        if not isinstance(raw, str) or not raw.strip():
            raise AgentOutputError("LLM trả về nội dung rỗng.")

        text = raw.strip()

        for candidate in BaseAgent._json_candidates(text):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if isinstance(data, dict):
                return data

            raise AgentOutputError(
                "LLM phải trả về JSON object, không phải "
                f"{type(data).__name__}."
            )

        preview = BaseAgent._shorten(text, 200)
        raise AgentOutputError(f"LLM trả về JSON không hợp lệ: {preview}")

    @staticmethod
    def _json_candidates(text):
        candidates = [text]

        if text.startswith("```"):
            without_fence = text.split("```")
            if len(without_fence) >= 3:
                body = without_fence[1]
                if body.lstrip().lower().startswith("json"):
                    body = body.lstrip()[4:]
                candidates.append(body.strip())

        start = text.find("{")
        end = text.rfind("}")

        if start != -1 and end > start:
            candidates.append(text[start:end + 1])

        return candidates

    @staticmethod
    def _shorten(text, limit):
        text = " ".join(str(text).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _with_repair_instruction(task, error):
        return (
            f"{task}\n\n"
            "LƯU Ý QUAN TRỌNG: lần trả lời trước không hợp lệ "
            f"({error}). Hãy trả về DUY NHẤT một JSON object đúng schema, "
            "không thêm markdown, không thêm giải thích."
        )

    def log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, **fields)

    def log_warning(self, event, message="", **fields):
        if self._log is not None:
            self._log.warning(event, message, **fields)
