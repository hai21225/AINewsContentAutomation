"""Đối tượng giả dùng chung cho test: không gọi mạng, không tốn chi phí."""

import json
from io import BytesIO
from types import SimpleNamespace


class FakeLlmClient:
    """LLM client giả, trả lần lượt các response đã xếp sẵn.

    Response có thể là dict/list (được serialize thành JSON), chuỗi thô,
    hoặc Exception (được raise ra ngoài).
    """

    provider_name = "fake"
    last_used_model = None

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, prompt, output_format=None):
        self.calls.append({"prompt": prompt, "output_format": output_format})

        if not self.responses:
            raise AssertionError("FakeLlmClient đã hết response.")

        response = self.responses.pop(0)

        if isinstance(response, BaseException):
            raise response

        self.last_used_model = "fake-model"

        if isinstance(response, (dict, list)):
            return json.dumps(response, ensure_ascii=False)

        return response


class FakeImageClient:
    """Image client giả, trả lần lượt bytes hoặc Exception."""

    provider_name = "fake"

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def generate(self, prompt, **options):
        self.calls.append({"prompt": prompt, **options})

        if not self.results:
            raise AssertionError("FakeImageClient đã hết kết quả.")

        result = self.results.pop(0)

        if isinstance(result, BaseException):
            raise result

        return result


class FakeHttpResponse:
    def __init__(self, status_code=200, text="", headers=None, content=b""):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.content = content

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Session giả cho requests: trả lần lượt response/exception."""

    def __init__(self, results):
        self.results = list(results)
        self.headers = {}
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append({"method": "GET", "url": url, **kwargs})
        return self._next()

    def post(self, url, **kwargs):
        self.requests.append({"method": "POST", "url": url, **kwargs})
        return self._next()

    def put(self, url, **kwargs):
        self.requests.append({"method": "PUT", "url": url, **kwargs})
        return self._next()

    def _next(self):
        if not self.results:
            raise AssertionError("FakeSession đã hết kết quả.")

        result = self.results.pop(0)

        if isinstance(result, BaseException):
            raise result

        return result


class FakeGeminiModels:
    def __init__(self, behaviours):
        self.behaviours = behaviours
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})

        if model not in self.behaviours:
            raise AssertionError(f"Không có behaviour cho model {model!r}.")

        behaviour = self.behaviours[model]

        if isinstance(behaviour, BaseException):
            raise behaviour

        if callable(behaviour):
            return behaviour()

        return SimpleNamespace(text=behaviour)


class FakeGeminiClient:
    def __init__(self, behaviours):
        self.models = FakeGeminiModels(behaviours)


def image_bytes(fmt="PNG", size=(2, 2), color=(255, 255, 255)):
    """Tạo bytes ảnh thật (dùng Pillow) để test kiểm tra/giải mã."""
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()
