

class SuperVisorAgent():

    def __init__(self, writerAgent, checkerAgent, maxRetries=3):
        self.writerAgent = writerAgent
        self.checkerAgent = checkerAgent
        self.maxRetries = maxRetries

    def run(self, article):
        a = 0
        # writer_result = self.writerAgent.run(article)
        # candidates = writer_result["candidates"]

        # if len(candidates) == 0:
        #     raise ValueError("WriterAgent không tạo được ứng viên hợp lệ nào.")

        while a < self.writerAgent._maxRetries:
            print(f"Đang yêu cầu Writer tạo ứng viên lần thử thứ {a + 1}")
            writer_result = self.writerAgent.run(article)
            candidates = writer_result["candidates"]
            if len(candidates) > 0:
                print(f"Writer đã tạo {len(candidates)} ứng viên hợp lệ.")
                break
            else:
                a += 1

        if len(candidates) == 0:
            raise ValueError("WriterAgent không tạo được ứng viên hợp lệ nào.")

        highest_score_check = None
        current_candidate = None

        print("Bắt đầu kiểm tra các ứng viên...")
        for attempt in range(self.maxRetries + 1):
            checked_candidates = self.checkerAgent.run_for_candidates(article, candidates)
            current_best_check = self._find_highest_score(checked_candidates)

            if highest_score_check is None:
                highest_score_check = current_best_check
            else:
                if current_best_check["score"] > highest_score_check["score"]:
                    highest_score_check = current_best_check

            current_candidate = current_best_check["candidate"]

            print(
                f"Vòng kiểm tra {attempt + 1}: "
                f"điểm cao nhất là {current_best_check['score']}/10."
            )

            if attempt == self.maxRetries:
                break

            print("Đang yêu cầu Writer revise bản tốt nhất của vòng này...")

            revised_result = self.writerAgent.revise(
                article,
                current_candidate,
                current_best_check
            )

            revised_result["title"] = article["title"]
            revised_result["candidate_id"] = current_best_check["candidate_id"]
            revised_result["word_count"] = self.writerAgent._count_words(
                revised_result["tts_text"]
            )
            revised_result["validation_errors"] = self.writerAgent._decision(
                revised_result
            )

            if len(revised_result["validation_errors"]) == 0:
                candidates = []
                candidates.append(revised_result)
            else:
                print("Bản revise bị lỗi kỹ thuật, giữ lại bản trước đó.")
                candidates = []
                candidates.append(current_candidate)

        print(
            f"Hoàn tất kiểm tra. Chọn bản có điểm cao nhất: "
            f"{highest_score_check['score']}/10."
        )

        #print (highest_score_check["candidate"])
        result = self._clean_final(highest_score_check["candidate"])
        return result

    def _find_highest_score(self, checked_candidates):
        highest_score = None
        best_check = None

        for checked_candidate in checked_candidates:
            score = checked_candidate["score"]

            if highest_score is None:
                highest_score = score
                best_check = checked_candidate
            elif score > highest_score:
                highest_score = score
                best_check = checked_candidate

        return best_check
    

    def _clean_final(self, result):
        words = result["tts_text"].split()

        result["tts_text"] = " ".join(
            word for word in words
            if not word.startswith("#")
        )

        cleanHastag=[]
        for hashtag in result.get("hashtags", []):
            hashtag = hashtag.strip()

            # bỏ khoảng trắng
            hashtag = hashtag.replace(" ", "")

            # thêm # nếu thiếu
            if not hashtag.startswith("#"):
                hashtag = "#" + hashtag

            if hashtag != "#":
                cleanHastag.append(hashtag)

        # bỏ trùng + tối đa 5
        result["hashtags"] = list(dict.fromkeys(cleanHastag))[:5]

        return result

