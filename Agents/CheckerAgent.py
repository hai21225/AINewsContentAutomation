from Agents.BaseAgent import BaseAgent
import json


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

class CheckerAgent(BaseAgent):

    def __init__(self, config, llmClient):
        self._llmClient = llmClient
        self._role = config["role"]
        self._checkerPrompt = config["prompt"]

        super().__init__(
            self._llmClient,
            self._role
        )

    def run(self, article, writerOutput):

        task = f"""
            {self._checkerPrompt}

            BÀI BÁO NGUỒN:

            Tiêu đề:
            {article["title"]}

            Mô tả:
            {article.get("des", article.get("description", ""))}

            Nội dung:
            {article["content"]}


            NỘI DUNG WRITER ĐÃ TẠO:

            {writerOutput["tts_text"]}

            HASHTAGS:
            {writerOutput["hashtags"]}


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
            revision_instruction phải nêu cụ thể cần sửa đoạn nào, sửa thành nội dung gì
            và phải dựa trên bài báo nguồn.
            """

        result = self.generate(
            task,
            outputFormat=CHECKER_OUTPUT_FORMAT
        )

        resultcheck = json.loads(result)
        #print(resultcheck)
        return resultcheck

    def run_for_candidates(self, article, candidates):
        checked_candidates = []

        for candidate in candidates:
            check = self.run(article, candidate)
            check["candidate_id"] = candidate["candidate_id"]
            check["candidate"] = candidate
            checked_candidates.append(check)

        return checked_candidates

