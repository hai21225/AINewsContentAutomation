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
        self._candidateCount = config.get("candidate_count", 3)
        self._minimumAcceptedWords = config.get("minimum_accepted_word", self._minWords)
        self._maximumAcceptedWords = config.get("maximum_accepted_word", self._maxWords)
        super().__init__(
            self._llmClient,
            self._role
        )

        self._writerPrompt = config["prompt"]

    def _count_words(self, text):
        return len(text.split())

    def run(self, article):
        print ("Đang tiến hành tóm tắt bài báo...")
        title = article["title"]
        description = article.get("des", article.get("description", ""))
        content = article["content"]

        base_task = f"""
            {self._writerPrompt}

            YÊU CẦU BẮT BUỘC:
            - Mục tiêu tts_text từ {self._minWords} đến {self._maxWords} từ (khoảng 1 phút đọc).
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

        attempt_results = []
        for attempt in range(self._candidateCount):

            result = self.generate(
                base_task,
                outputFormat=WRITER_OUTPUT_FORMAT
            )

            resultjson = json.loads(result)

            tts_text = resultjson["tts_text"].strip()
            word_count = self._count_words(tts_text)

            print(f"Lần thứ {attempt + 1} sinh ra: {word_count} từ")

            candidate = self._prepare_result(resultjson, title, tts_text)
            candidate["word_count"] = word_count
            candidate["candidate_id"] = attempt
            candidate["validation_errors"] = self._decision(candidate)

            if len(candidate["validation_errors"]) == 0:
                attempt_results.append(candidate)
                print(f"Ứng viên {attempt + 1} hợp lệ để Checker đánh giá.")
            else:
                print(f"Ứng viên {attempt + 1} bị loại:")
                print(candidate["validation_errors"])

        return {
            "candidates": attempt_results,
            "article_title": title,
            "writer_attempt_count": self._candidateCount
        }


    def _decision(self, attempts):
        errors = []

        if "tts_text" not in attempts:
            errors.append("Thiếu trường tts_text.")
        else:
            if attempts["tts_text"] == "":
                errors.append("tts_text đang rỗng.")

            if "#" in attempts["tts_text"]:
                errors.append("tts_text không được chứa hashtag.")

        if "hashtags" not in attempts:
            errors.append("Thiếu trường hashtags.")
        else:
            if len(attempts["hashtags"]) < 3:
                errors.append("Phải có ít nhất 3 hashtag.")
            if len(attempts["hashtags"]) > 5:
                errors.append("Không được có quá 5 hashtag.")

        word_count = attempts["word_count"]
        if word_count < self._minimumAcceptedWords:
            errors.append(
                f"Nội dung quá ngắn: {word_count} từ, tối thiểu chấp nhận là {self._minimumAcceptedWords} từ."
            )
        if word_count > self._maximumAcceptedWords:
            errors.append(
                f"Nội dung quá dài: {word_count} từ, tối đa chấp nhận là {self._maximumAcceptedWords} từ."
            )

        return errors

    def _prepare_result(self, resultjson, title, tts_text):

        resultjson["tts_text"] = tts_text
        resultjson["title"] = title
        cleaned_hashtags = []

        for hashtag in resultjson["hashtags"]:
            cleaned_hashtag = hashtag.strip()
            cleaned_hashtags.append(cleaned_hashtag)

        resultjson["hashtags"] = cleaned_hashtags

        return resultjson
    

    def revise(self, article, previousResult, feedback):
        print("Đang yêu cầu chỉnh sửa...")
        task = f"""
        {self._writerPrompt}

        BÀI BÁO NGUỒN:

        {article["content"]}

        BẢN TRƯỚC:

        {previousResult["tts_text"]}

        PHẢN HỒI TỪ CHECKER:

        {feedback["revision_instruction"]}

        Hãy sửa lại nội dung dựa trên phản hồi trên.

        Không được sửa những phần đang đúng nếu không cần thiết.
        Không được thêm thông tin ngoài bài báo nguồn.

        Mục tiêu tts_text từ {self._minWords} đến {self._maxWords} từ.
        """

        result = self.generate(
            task,
            outputFormat=WRITER_OUTPUT_FORMAT
        )

        return json.loads(result)
