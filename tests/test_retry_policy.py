import pytest

from Errors import ConfigurationError, ExternalServiceError
from Retry import RetryPolicy, retry_after_seconds


def transient(status=503, retry_after=None):
    return ExternalServiceError(
        "lỗi tạm thời",
        service="svc",
        status=status,
        retryable=True,
        retry_after=retry_after,
    )


def permanent(status=400):
    return ExternalServiceError(
        "lỗi vĩnh viễn", service="svc", status=status, retryable=False
    )


class FakeRandom:
    def __init__(self, value):
        self.value = value

    def uniform(self, low, high):
        return self.value


class RecordingLog:
    def __init__(self):
        self.calls = []

    def warning(self, event, message="", **fields):
        self.calls.append({"event": event, "message": message, "fields": fields})


def build_policy(**kwargs):
    slept = []
    kwargs.setdefault("sleep", slept.append)
    return RetryPolicy(**kwargs), slept


# ----------------------------------------------------------------------
# Thực thi retry
# ----------------------------------------------------------------------


def test_succeeds_on_first_attempt_without_sleeping():
    policy, slept = build_policy()

    assert policy.run(lambda: "ok") == "ok"
    assert slept == []


def test_retries_transient_error_then_succeeds():
    calls = []

    def operation():
        calls.append(1)

        if len(calls) == 1:
            raise transient()

        return "ok"

    policy, slept = build_policy(max_attempts=3, jitter_ratio=0)

    assert policy.run(operation) == "ok"
    assert len(calls) == 2
    assert slept == [1.0]


def test_permanent_error_is_not_retried():
    calls = []

    def operation():
        calls.append(1)
        raise permanent()

    policy, slept = build_policy(max_attempts=3)

    with pytest.raises(ExternalServiceError) as excinfo:
        policy.run(operation)

    assert excinfo.value.retryable is False
    assert len(calls) == 1
    assert slept == []


def test_exhaustion_reraises_last_error():
    calls = []

    def operation():
        calls.append(1)
        raise transient()

    policy, slept = build_policy(max_attempts=3, jitter_ratio=0)

    with pytest.raises(ExternalServiceError) as excinfo:
        policy.run(operation)

    assert excinfo.value.status == 503
    assert len(calls) == 3
    assert len(slept) == 2  # chỉ chờ giữa các lần, không chờ sau lần cuối


def test_max_attempts_one_disables_retry():
    calls = []

    def operation():
        calls.append(1)
        raise transient()

    policy, slept = build_policy(max_attempts=1)

    with pytest.raises(ExternalServiceError):
        policy.run(operation)

    assert len(calls) == 1
    assert slept == []


# ----------------------------------------------------------------------
# Backoff / Retry-After
# ----------------------------------------------------------------------


def test_retry_after_is_honoured_exactly():
    policy, _ = build_policy(max_backoff_seconds=30)

    assert policy.delay_for(1, retry_after=2) == 2.0


def test_retry_after_is_capped_by_max_backoff():
    policy, _ = build_policy(max_backoff_seconds=8)

    assert policy.delay_for(1, retry_after=100) == 8.0


def test_backoff_grows_exponentially_without_jitter():
    policy, _ = build_policy(
        backoff_seconds=2.0, max_backoff_seconds=8.0, jitter_ratio=0
    )

    assert policy.delay_for(1) == 2.0
    assert policy.delay_for(2) == 4.0
    assert policy.delay_for(3) == 8.0
    assert policy.delay_for(4) == 8.0  # chạm trần


def test_jitter_ratio_scales_delay():
    policy, _ = build_policy(
        backoff_seconds=2.0,
        max_backoff_seconds=8.0,
        jitter_ratio=0.5,
        random_source=FakeRandom(0.5),
    )

    # base 2.0 * uniform(0.5, 1.0) = 2.0 * 0.5 = 1.0
    assert policy.delay_for(1) == 1.0


def test_zero_backoff_returns_zero():
    policy, _ = build_policy(backoff_seconds=0, max_backoff_seconds=0)

    assert policy.delay_for(1) == 0.0


def test_log_receives_retry_context():
    log = RecordingLog()

    def operation():
        raise transient()

    policy, _ = build_policy(max_attempts=2, jitter_ratio=0)

    with pytest.raises(ExternalServiceError):
        policy.run(
            operation,
            event_name="rss_retry",
            description="https://example.com/rss",
            service="rss",
            fields={"article_id": "abc"},
            log=log,
        )

    assert log.calls[0]["event"] == "rss_retry"
    fields = log.calls[0]["fields"]
    assert fields["attempt"] == 1
    assert fields["max_attempts"] == 2
    assert fields["status"] == 503
    assert fields["service"] == "rss"
    assert fields["target"] == "https://example.com/rss"
    assert fields["article_id"] == "abc"


# ----------------------------------------------------------------------
# Cấu hình
# ----------------------------------------------------------------------


def test_from_config_reads_flat_keys_and_max_retries_alias():
    policy = RetryPolicy.from_config({"max_retries": 4, "backoff_seconds": 3})

    assert policy.max_attempts == 4
    assert policy.backoff_seconds == 3.0


def test_from_config_later_dict_wins():
    policy = RetryPolicy.from_config(
        {"max_attempts": 5},
        {"max_attempts": 2},
    )

    assert policy.max_attempts == 2


def test_from_config_reads_nested_retry_block():
    policy = RetryPolicy.from_config(
        {"retry": {"max_attempts": 7, "jitter_ratio": 0.25}}
    )

    assert policy.max_attempts == 7
    assert policy.jitter_ratio == 0.25


def test_from_config_uses_defaults_when_value_missing():
    policy = RetryPolicy.from_config({"other": 1})

    assert policy.max_attempts == RetryPolicy().max_attempts


@pytest.mark.parametrize(
    "config",
    [
        {"max_attempts": 0},
        {"max_attempts": -1},
        {"backoff_seconds": -1},
        {"max_backoff_seconds": -1},
        {"jitter_ratio": 2},
        {"jitter_ratio": -0.1},
        {"backoff_seconds": 5, "max_backoff_seconds": 1},
    ],
)
def test_invalid_config_raises_configuration_error(config):
    with pytest.raises(ConfigurationError):
        RetryPolicy.from_config(config)


def test_defaults_are_sane():
    policy = RetryPolicy()

    assert policy.max_attempts == 3
    assert policy.backoff_seconds == 1.0
    assert policy.max_backoff_seconds == 8.0
    assert policy.jitter_ratio == 0.5


# ----------------------------------------------------------------------
# retry_after_seconds
# ----------------------------------------------------------------------


def test_retry_after_seconds_parses_header():
    assert retry_after_seconds({"Retry-After": "3"}) == 3.0
    assert retry_after_seconds({"retry-after": "1.5"}) == 1.5


def test_retry_after_seconds_handles_missing_and_invalid():
    assert retry_after_seconds(None) is None
    assert retry_after_seconds({}) is None
    assert retry_after_seconds({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert retry_after_seconds({"Retry-After": "-5"}) == 0.0
