"""Che bí mật trước khi ghi log hoặc đưa vào thông báo lỗi.

Dùng chung cho ``Services.LogService`` và mọi client gọi dịch vụ ngoài, để
API key/token không lọt vào log hay traceback.
"""

import re

_REDACTED = "***"

#: Khoá có tên gợi ý bí mật -> che toàn bộ giá trị.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|apikey|token|secret|password|authorization|bearer|credential)",
    re.IGNORECASE,
)

#: Tham số bí mật nằm trong query string của URL.
_SECRET_QUERY_PATTERN = re.compile(
    r"((?:api[_-]?key|key|token|access_token|signature)=)([^&\s\"']+)",
    re.IGNORECASE,
)


def redact(value):
    """Che bí mật trong chuỗi (an toàn với giá trị không phải chuỗi)."""
    if not isinstance(value, str):
        return value
    return _SECRET_QUERY_PATTERN.sub(r"\1" + _REDACTED, value)


def redact_fields(fields):
    """Che bí mật trong dict field theo cả tên khoá và giá trị."""
    clean = {}

    for key, value in fields.items():
        if isinstance(key, str) and _SECRET_KEY_PATTERN.search(key):
            clean[key] = _REDACTED
        elif isinstance(value, dict):
            clean[key] = redact_fields(value)
        elif isinstance(value, (list, tuple)):
            clean[key] = [
                redact_fields(item) if isinstance(item, dict) else redact(item)
                for item in value
            ]
        else:
            clean[key] = redact(value)

    return clean
