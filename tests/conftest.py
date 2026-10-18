import pytest

from Services.LogService import NullLogService


@pytest.fixture
def null_log():
    return NullLogService()


@pytest.fixture
def article():
    return {
        "id": "abc123",
        "title": "Tiêu đề bài kiểm thử",
        "des": "Mô tả ngắn của bài kiểm thử.",
        "content": "Nội dung bài báo. " * 60,
        "source_url": "https://example.com/bai-viet",
    }


@pytest.fixture
def articles(article):
    return [
        {**article, "id": "id-1", "title": "Bài 1"},
        {**article, "id": "id-2", "title": "Bài 2"},
        {**article, "id": "id-3", "title": "Bài 3"},
    ]


@pytest.fixture
def no_sleep():
    """Hàm sleep giả để test không phải chờ thật."""
    calls = []

    def sleep(seconds):
        calls.append(seconds)

    sleep.calls = calls
    return sleep
