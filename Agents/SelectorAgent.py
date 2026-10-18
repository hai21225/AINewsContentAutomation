import json

from Agents.BaseAgent import BaseAgent
from Errors import ConfigurationError


def build_output_format(article_count):
    """Schema chọn bài, khớp đúng số lượng cần chọn.

    Trước đây schema cố định ``minItems/maxItems = 5`` trong khi cấu hình
    ``article_count`` có thể là 1, gây mâu thuẫn giữa schema và prompt.
    """
    return {
        "type": "object",
        "properties": {
            "selected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "reason"],
                },
                "minItems": article_count,
                "maxItems": article_count,
            }
        },
        "required": ["selected"],
    }


class SelectorAgent(BaseAgent):
    """Chọn ra ``article_count`` bài viết phù hợp nhất từ danh sách ứng viên."""

    def __init__(self, config, llm_client, log_service=None):
        super().__init__(
            llm_client,
            config["role"],
            log_service=log_service,
            json_max_attempts=config.get("json_max_attempts"),
        )

        self._prompt = config["prompt"]
        self._article_count = int(config.get("article_count", 5))

        if self._article_count < 1:
            raise ConfigurationError(
                "article_count của selector phải lớn hơn hoặc bằng 1."
            )

    @property
    def article_count(self):
        return self._article_count

    def run(self, articles):
        candidates = [
            {
                "id": article["id"],
                "title": article.get("title", ""),
                "des": article.get("des", ""),
            }
            for article in articles
        ]

        if not candidates:
            return []

        count = min(self._article_count, len(candidates))

        task = f"""
            {self._prompt}

            Chọn đúng {count} bài. Chỉ được chọn id có trong danh sách.
            Trả về reason ngắn gọn bằng tiếng Việt cho từng bài.
            Không trả về title, des, content hoặc bất kỳ trường nào khác.

            DANH SÁCH BÀI:
            {self._format_candidates(candidates)}
            """

        result = self.generate_json(task, build_output_format(count))

        valid_ids = {article["id"] for article in articles}
        selected = []
        seen = set()

        for item in result.get("selected", []):
            if not isinstance(item, dict):
                continue

            article_id = item.get("id")

            if article_id not in valid_ids or article_id in seen:
                continue

            seen.add(article_id)
            selected.append(
                {
                    "id": article_id,
                    "reason": str(item.get("reason", "")).strip(),
                }
            )

        if len(selected) < count:
            self.log_warning(
                "selector_incomplete",
                f"Selector chỉ trả về {len(selected)}/{count} bài hợp lệ.",
            )

        return selected[:count]

    def _format_candidates(self, candidates):
        return json.dumps(candidates, ensure_ascii=False, indent=2)
