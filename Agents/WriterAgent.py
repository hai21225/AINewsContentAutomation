import json

from Agents.BaseAgent import BaseAgent

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


class WriterAgent(BaseAgent):
    def __init__(self, config, llmClient):

        self._llmClient = llmClient
        self._role = config["role"]
        self._minWords = config["min_word"]
        self._maxWords = config["max_word"]
        self._maxRetries = config["max_retries"]
        super().__init__(
            self._llmClient,
            self._role
        )

        self._writerPrompt = config["prompt"]

    def _count_words(self, text):
        return len(text.split())
    
    def run(self, article):
        title = article["title"]
        description = article["description"]
        content = article["content"]

        base_task = f"""
            {self._writerPrompt}

            YÊU CẦU BẮT BUỘC:
            - Bắt buộc tts_text từ {self._minWords} đến {self._maxWords} từ (khoảng 1 phút đọc).
            - Chỉ sử dụng thông tin có trong bài báo nguồn.
            - Không tự thêm dữ kiện, suy đoán hoặc nhận xét.
            - Không đặt hashtag trong tts_text.
            - Hashtag chỉ được đặt trong trường hashtags, chuẩn hastags lấy ra là dùng được phải đi kèm "#".
            - Hãy tận dụng các chi tiết, số liệu, nguyên nhân, kết quả và bối cảnh có trong bài báo để đạt đủ độ dài cũng như không vượt quá yêu cầu.

            BÀI BÁO NGUỒN:

            Tiêu đề:
            {title}

            Mô tả:
            {description}

            Nội dung:
            {content}
            """

        task = base_task

        attempt_results = []
        for attempt in range(self._maxRetries):

            result = self.generate(
                task,
                outputFormat=WRITER_OUTPUT_FORMAT
            )

            resultjson = json.loads(result)

            tts_text = resultjson["tts_text"].strip()
            word_count = self._count_words(tts_text)

            print(f"Attempt {attempt + 1}: {word_count} từ")

            attempt_results.append({
                "resultjson": resultjson,
                "tts_text": tts_text,
                "word_count": word_count
            })

            if self._maxWords >= word_count >= self._minWords:
                return self._prepare_result(resultjson, title, tts_text)

            # Retry nhưng vẫn giữ bài báo nguồn
            if word_count < self._minWords:
                task = f"""
                    {base_task}

                    KẾT QUẢ LẦN TRƯỚC:

                    {tts_text}

                    Kết quả trên có {word_count} từ nên chưa đạt yêu cầu {self._minWords} đến {self._maxWords} từ.

                    Hãy viết lại toàn bộ JSON.

                    Đặc biệt:
                    - Bắt buộc tts_text từ {self._minWords} đến {self._maxWords} từ.
                    - Nếu quá ngắn, hãy khai thác thêm các thông tin có sẵn trong bài báo nguồn.
                    - Không được thêm thông tin không có trong nguồn.
                    """
            elif word_count > self._maxWords:
                task = f"""
                    {base_task}

                    KẾT QUẢ LẦN TRƯỚC:

                    {tts_text}

                    Kết quả trên có {word_count} từ nên đã vượt quá yêu cầu {self._minWords} đến {self._maxWords} từ.

                    Hãy viết lại toàn bộ JSON.

                    Đặc biệt:
                    - Bắt buộc tts_text từ {self._minWords} đến {self._maxWords} từ.
                    - Nếu quá dài, hãy rút gọn nội dung, giữ lại các thông tin quan trọng.
                    - Không được thêm thông tin không có trong nguồn.
                    """



        best = self._decision(attempt_results)

        return self._prepare_result(
            best["resultjson"],
            title,
            best["tts_text"]
        )

        # raise ValueError(
        #     f"WriterAgent không tạo được tts_text "
        #     f"{self.MIN_WORDS}-{self.MAX_WORDS} từ "
        #     f"sau {self.MAX_RETRIES} lần thử."
        # )
    


    def _decision(self, attempts):

        bestAttempt = None
        bestDistance =None
        distance = 0

        for attempt in attempts:
            wordCount = attempt["word_count"]

            if wordCount < self._minWords:
                distance = self._minWords - wordCount

            elif wordCount > self._maxWords:
                distance = wordCount - self._maxWords
            else:
                distance = 0

            if bestDistance is None:
                bestDistance = distance
                bestAttempt = attempt

            elif distance < bestDistance:
                bestDistance = distance
                bestAttempt = attempt

        return bestAttempt

    def _prepare_result(self, resultjson, title, tts_text):
        resultjson["tts_text"] = tts_text
        resultjson["title"] = title
        resultjson["hashtags"] = [
            hashtag.strip()
            for hashtag in resultjson["hashtags"]
        ]

        return resultjson