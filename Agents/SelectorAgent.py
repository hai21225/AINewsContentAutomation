import json

from Agents.BaseAgent import BaseAgent


SELECTOR_OUTPUT_FORMAT = {
    "type": "object",
    "properties": {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "reason": {"type": "string"}
                },
                "required": ["id", "reason"]
            },
            "minItems": 5,
            "maxItems": 5
        }
    },
    "required": ["selected"]
}


class SelectorAgent(BaseAgent):
    def __init__(self, config, llmClient):
        super().__init__(llmClient, config["role"])
        self._prompt = config["prompt"]
        self._articleCount = config.get("article_count", 5)

    def run(self, articles):
        candidates = [
            {"id": a["id"], "title": a["title"], "des": a["des"]}
            for a in articles
        ]
        task = f"""
        {self._prompt}

        Chọn đúng {self._articleCount} bài. Chỉ được chọn id có trong danh sách.
        Trả về reason ngắn gọn bằng tiếng Việt cho từng bài.
        Không trả về title, des, content hoặc bất kỳ trường nào khác.

        DANH SÁCH BÀI:
        {json.dumps(candidates, ensure_ascii=False, indent=2)}
        """
        result = json.loads(self.generate(task, SELECTOR_OUTPUT_FORMAT))
        valid_ids = {article["id"] for article in articles}
        selected = [
            item for item in result.get("selected", [])
            if item.get("id") in valid_ids
        ]
        return selected[:self._articleCount]
