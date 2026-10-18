from Errors import AppError, AgentOutputError

MAX_HASHTAGS = 5


class WriterSupervisor:
    """Điều phối vòng writer -> checker -> revise và chọn bản tốt nhất.

    Trách nhiệm:
    - yêu cầu writer sinh ứng viên (có giới hạn số vòng);
    - chấm điểm bằng checker và chọn ứng viên điểm cao nhất;
    - yêu cầu revise khi chưa đạt ngưỡng, dừng khi hết vòng hoặc khi bản
      revise không hợp lệ;
    - trả về ứng viên đã làm sạch (tts_text, hashtags, title).

    Không truy cập thuộc tính private của agent khác: mọi thứ cần dùng đều
    qua thuộc tính/phương thức công khai.
    """

    def __init__(self, writer_agent, checker_agent, max_retries=3,
                 quality_threshold=None, log_service=None):
        self.writer_agent = writer_agent
        self.checker_agent = checker_agent
        self.max_retries = max(0, int(max_retries))
        self._log = log_service

        if quality_threshold is None:
            quality_threshold = checker_agent.minimum_score

        self.quality_threshold = float(quality_threshold)

    def run(self, article):
        candidates = self._produce_candidates(article)

        best_check = None

        for round_index in range(self.max_retries + 1):
            round_number = round_index + 1

            checked_candidates = self.checker_agent.run_for_candidates(
                article, candidates
            )

            current_best = self._find_highest_score(checked_candidates)

            if current_best is None:
                raise AgentOutputError(
                    "Checker không trả về kết quả chấm điểm hợp lệ nào."
                )

            if best_check is None or current_best["score"] > best_check["score"]:
                best_check = current_best

            self.log_info(
                "supervisor_round",
                f"Vòng kiểm tra {round_number}: điểm cao nhất "
                f"{current_best['score']}/10.",
                round=round_number,
                score=current_best["score"],
                passed=current_best["passed"],
            )

            if best_check["passed"] and best_check["score"] >= self.quality_threshold:
                self.log_info(
                    "supervisor_quality_reached",
                    f"Đạt ngưỡng chất lượng {self.quality_threshold}.",
                    score=best_check["score"],
                    round=round_number,
                )
                break

            if round_index >= self.max_retries:
                break

            revised = self._try_revise(article, best_check, round_number)

            if revised is None:
                break

            candidates = [revised]

        self.log_info(
            "supervisor_done",
            f"Chọn bản có điểm cao nhất: {best_check['score']}/10.",
            score=best_check["score"],
            passed=best_check["passed"],
        )

        return self.clean_final(best_check["candidate"])

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    def _produce_candidates(self, article):
        max_attempts = max(1, self.writer_agent.max_retries)

        for attempt in range(1, max_attempts + 1):
            writer_result = self.writer_agent.run(article)
            candidates = writer_result.get("candidates") or []

            if candidates:
                self.log_info(
                    "writer_candidates_ready",
                    f"Writer tạo {len(candidates)} ứng viên hợp lệ.",
                    attempt=attempt,
                    candidate_count=len(candidates),
                )
                return candidates

            self.log_warning(
                "writer_no_candidate",
                f"Lần {attempt}/{max_attempts} không có ứng viên hợp lệ.",
                attempt=attempt,
            )

        raise AgentOutputError(
            "WriterAgent không tạo được ứng viên hợp lệ nào."
        )

    def _try_revise(self, article, best_check, round_number):
        candidate = best_check.get("candidate")

        if not isinstance(candidate, dict):
            self.log_warning(
                "supervisor_revise_skipped",
                "Không có ứng viên để sửa.",
                round=round_number,
            )
            return None

        try:
            revised = self.writer_agent.revise(article, candidate, best_check)
        except AppError as error:
            self.log_warning(
                "supervisor_revise_failed",
                f"Không sửa được bản tốt nhất: {error}",
                round=round_number,
                error_type=type(error).__name__,
            )
            return None

        validation_errors = revised.get("validation_errors") or []

        if validation_errors:
            self.log_warning(
                "supervisor_revise_rejected",
                "Bản revise bị lỗi kỹ thuật, giữ lại bản trước đó.",
                round=round_number,
                errors=validation_errors,
            )
            return None

        return revised

    @staticmethod
    def _find_highest_score(checked_candidates):
        best_check = None

        for checked_candidate in checked_candidates:
            if not isinstance(checked_candidate, dict):
                continue

            score = checked_candidate.get("score")

            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue

            if best_check is None or score > best_check["score"]:
                best_check = checked_candidate

        return best_check

    def clean_final(self, result):
        """Chuẩn hoá bản cuối: bỏ hashtag khỏi tts_text, làm sạch hashtags."""
        if not isinstance(result, dict):
            raise AgentOutputError("Bản cuối không phải dict.")

        tts_text = result.get("tts_text")
        tts_text = tts_text if isinstance(tts_text, str) else ""

        words = [word for word in tts_text.split() if not word.startswith("#")]
        result["tts_text"] = " ".join(words)

        hashtags = self.writer_agent.normalize_hashtags(
            result.get("hashtags", [])
        )
        result["hashtags"] = list(dict.fromkeys(hashtags))[:MAX_HASHTAGS]

        return result

    def log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, **fields)

    def log_warning(self, event, message="", **fields):
        if self._log is not None:
            self._log.warning(event, message, **fields)
