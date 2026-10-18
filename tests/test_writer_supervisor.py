import pytest

from Agents.WriterAgent import WriterAgent
from Agents.WriterSupervisor import WriterSupervisor
from Errors import AgentOutputError, ExternalServiceError


class StubWriter:
    """Writer giả: trả các batch ứng viên đã xếp sẵn."""

    normalize_hashtags = staticmethod(WriterAgent.normalize_hashtags)

    def __init__(self, batches, revisions=None):
        self.max_retries = 2
        self._batches = list(batches)
        self._revisions = list(revisions or [])
        self.revise_calls = []

    def run(self, article):
        if not self._batches:
            raise AssertionError("StubWriter đã hết batch.")
        return {"candidates": self._batches.pop(0)}

    def revise(self, article, previous_result, feedback):
        self.revise_calls.append(
            {"previous": previous_result, "instruction": feedback.get("revision_instruction")}
        )
        response = self._revisions.pop(0)

        if isinstance(response, BaseException):
            raise response

        return response


class StubChecker:
    """Checker giả: trả điểm đã xếp sẵn cho từng vòng."""

    def __init__(self, rounds):
        self.minimum_score = 8.0
        self._rounds = list(rounds)
        self.call_count = 0

    def run_for_candidates(self, article, candidates):
        self.call_count += 1

        if not self._rounds:
            raise AssertionError("StubChecker đã hết vòng.")

        scores = self._rounds.pop(0)
        checked = []

        for candidate, (score, passed) in zip(candidates, scores):
            checked.append(
                {
                    "score": score,
                    "passed": passed,
                    "issues": [],
                    "revision_instruction": "Sửa lại.",
                    "candidate_id": candidate.get("candidate_id"),
                    "candidate": candidate,
                }
            )

        return checked


def candidate(candidate_id, text="Nội dung bản tin đã đủ dài.", hashtags=None):
    return {
        "candidate_id": candidate_id,
        "tts_text": text,
        "hashtags": hashtags if hashtags is not None else ["#tin", "#video", "#news"],
        "title": "Tiêu đề",
        "word_count": len(text.split()),
        "validation_errors": [],
    }


def test_picks_candidate_with_highest_score(article):
    writer = StubWriter([[candidate(0), candidate(1)]])
    checker = StubChecker([[(5.0, False), (9.0, True)]])

    supervisor = WriterSupervisor(writer, checker, log_service=None)

    result = supervisor.run(article)

    assert result["candidate_id"] == 1
    assert writer.revise_calls == []
    assert checker.call_count == 1


def test_low_score_triggers_revision(article):
    improved = candidate(0, text="Bản đã sửa và đủ dài để đạt điểm cao.")
    writer = StubWriter([[candidate(0)],], revisions=[improved])
    checker = StubChecker([[(4.0, False)], [(9.0, True)]])

    supervisor = WriterSupervisor(writer, checker, log_service=None)

    result = supervisor.run(article)

    assert checker.call_count == 2
    assert len(writer.revise_calls) == 1
    assert result["tts_text"] == improved["tts_text"]


def test_keeps_best_candidate_when_revision_fails(article):
    writer = StubWriter(
        [[candidate(0), candidate(1)]],
        revisions=[ExternalServiceError("LLM sập")],
    )
    checker = StubChecker([[(5.0, False), (7.0, False)]])

    supervisor = WriterSupervisor(writer, checker, log_service=None)

    result = supervisor.run(article)

    assert result["candidate_id"] == 1
    assert checker.call_count == 1


def test_invalid_revision_is_rejected(article):
    bad_revision = {**candidate(0), "validation_errors": ["tts_text đang rỗng."]}
    writer = StubWriter([[candidate(0)]], revisions=[bad_revision])
    checker = StubChecker([[(3.0, False)]])

    supervisor = WriterSupervisor(writer, checker, log_service=None)

    result = supervisor.run(article)

    assert checker.call_count == 1
    assert result["candidate_id"] == 0


def test_quality_threshold_stops_early(article):
    writer = StubWriter([[candidate(0)]])
    checker = StubChecker([[(8.5, True)]])

    supervisor = WriterSupervisor(
        writer, checker, quality_threshold=8.0, log_service=None
    )

    supervisor.run(article)

    assert writer.revise_calls == []


def test_max_retries_zero_skips_revision(article):
    writer = StubWriter([[candidate(0)]])
    checker = StubChecker([[(2.0, False)]])

    supervisor = WriterSupervisor(writer, checker, max_retries=0, log_service=None)

    supervisor.run(article)

    assert writer.revise_calls == []
    assert checker.call_count == 1


def test_writer_producing_no_candidates_raises(article):
    writer = StubWriter([[], []])
    checker = StubChecker([])

    supervisor = WriterSupervisor(writer, checker, log_service=None)

    with pytest.raises(AgentOutputError):
        supervisor.run(article)


def test_all_invalid_scores_raise(article):
    writer = StubWriter([[candidate(0)]])
    checker = StubChecker([[(None, False)]])

    supervisor = WriterSupervisor(writer, checker, log_service=None)

    with pytest.raises(AgentOutputError):
        supervisor.run(article)


def test_clean_final_removes_hashtags_from_narration(article):
    writer = StubWriter([[candidate(0)]])
    checker = StubChecker([[(9.0, True)]])
    supervisor = WriterSupervisor(writer, checker, log_service=None)

    cleaned = supervisor.clean_final(
        {
            "tts_text": "Nội dung #tin tục #video",
            "hashtags": ["#tin", "#tin", "#video", "#news", "#a", "#b"],
        }
    )

    assert "#" not in cleaned["tts_text"]
    assert cleaned["tts_text"] == "Nội dung tục"
    assert cleaned["hashtags"] == ["#tin", "#video", "#news", "#a", "#b"]
