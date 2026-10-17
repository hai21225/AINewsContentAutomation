import os
import time
from google import genai
from google.genai import types,errors

from LLM.AIClient import AiClient

from Console import Console


def _strip_unsupported_schema_keys(schema):
    """Loại bỏ các key schema mà Gemini Developer API không hỗ trợ.

    API của Gemini (chế độ Developer API) từ chối `additionalProperties`,
    trong khi schema dùng chung cho nhiều provider vẫn cần key này.
    """
    if isinstance(schema, dict):
        return {
            key: _strip_unsupported_schema_keys(value)
            for key, value in schema.items()
            if key not in ("additionalProperties", "additional_properties")
        }

    if isinstance(schema, list):
        return [_strip_unsupported_schema_keys(item) for item in schema]

    return schema


class GeminiClient(AiClient):
    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

    def __init__(self, provider_config, model_config):

        api_key_env = provider_config["api_key_env"]
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise ValueError(
                f"Không tìm thấy API key trong biến môi trường '{api_key_env}'."
            )

        self._client = genai.Client(api_key=api_key)
        self._model = model_config["model"]
        self._options = model_config.get("options",{})
        self._fallback_model = model_config.get("fallback_model", {})
        self._max_retries = model_config.get("max_retries", 2)


    def _generate_with_model(self, model, prompt, outputFormat):
            config_args = {
                "temperature": self._options.get("temperature", 0.2)
            }

            if outputFormat is not None:
                config_args["response_mime_type"] = "application/json"
                config_args["response_schema"] = (
                    _strip_unsupported_schema_keys(outputFormat)
                )

            response = self._client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_args)
            )

            if not response.text:
                raise RuntimeError(
                    f"Model '{model}' trả về nội dung rỗng."
                )

            return response.text

    def generate(self, prompt, outputFormat=None):
        last_error = None

        # 1. Ưu tiên model chính
        for attempt in range(self._max_retries + 1):
            try:
                return self._generate_with_model(
                    self._model, prompt, outputFormat
                )

            except errors.APIError as exc:
                last_error = exc
                status = getattr(exc, "code", None)

                Console.loading(
                    f"[Gemini] Model chính '{self._model}' lỗi "
                    f"(HTTP {status}): {exc}"
                )

                if status not in self.RETRYABLE_STATUS_CODES:
                    break

                # Hết quota thường không giải quyết được bằng retry liên tục, nên chuyển sang model dự phòng.
                if status == 429:
                    break

                if attempt < self._max_retries:
                    delay = min(2 ** (attempt + 1), 8)
                    Console.loading(f"[Gemini] Thử lại sau {delay} giây...")
                    time.sleep(delay)

        # 2. Thử lần lượt các model dự phòng
        for fallback_model in self._fallback_model:
            try:
                Console.loading(
                    f"[Gemini] Chuyển sang model dự phòng: "
                    f"{fallback_model}"
                )

                result = self._generate_with_model(
                    fallback_model, prompt, outputFormat
                )

                Console.loading(
                    f"[Gemini] Model dự phòng "
                    f"'{fallback_model}' xử lý thành công."
                )
                return result

            except Exception as exc:
                last_error = exc
                Console.loading(
                    f"[Gemini] Model dự phòng "
                    f"'{fallback_model}' cũng thất bại: {exc}"
                )

        # 3. Tất cả model đều thất bại
        if last_error is not None:
            raise last_error

        raise RuntimeError("Không có model nào xử lý thành công.")
