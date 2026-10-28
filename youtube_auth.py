"""Lấy refresh token YouTube bằng OAuth2 (chạy MỘT lần trên máy có trình duyệt).

YouTube không cho upload bằng API key. Luồng chuẩn của Google là OAuth2
"installed app": mở trình duyệt, chủ kênh bấm đồng ý, Google trả
authorization code về ``http://localhost:<port>``; đổi code đó lấy
``refresh_token``. Từ đó pipeline chỉ cần refresh token để tự lấy access token
mỗi lần chạy — không cần mở trình duyệt nữa.

Cách dùng::

    python youtube_auth.py                 # đọc yt-secret/client_secret.json
    python youtube_auth.py --no-browser    # in URL, tự dán vào trình duyệt
    python youtube_auth.py --force         # lấy token mới (ghi đè token cũ)

Kết quả:

- ``yt-secret/token.json``: refresh token + scope (đã nằm trong .gitignore).
- ``.env``: cập nhật ``YT_CLIENT_ID``, ``YT_CLIENT_SECRET``, ``YT_REFRESH_TOKEN``
  (giữ nguyên comment và các biến khác). Dùng ``--no-env`` để bỏ qua bước này.

Script chỉ dùng ``requests`` + thư viện chuẩn, không cần thêm dependency.
Token/secret không được in ra màn hình hay ghi vào log.
"""

import argparse
import json
import os
import re
import secrets
import sys
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

from Console import Console
from Errors import AppError, ConfigurationError, ExternalServiceError, is_transient_status

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

#: ``youtube.upload`` để đăng video; ``youtube.readonly`` để tra video đã đăng
#: (chống đăng trùng của PublishService).
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)

DEFAULT_CLIENT_SECRET = Path("yt-secret") / "client_secret.json"
DEFAULT_TOKEN_FILE = Path("yt-secret") / "token.json"
DEFAULT_ENV_FILE = Path(".env")
DEFAULT_TIMEOUT_SECONDS = 300

#: Dòng ``KEY=value`` trong .env, chấp nhận cả ``export KEY=value``.
_ENV_LINE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<sep>\s*)=(?P<value>.*)$"
)


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Nhận redirect loopback từ Google và lưu query vào server."""

    def do_GET(self):  # noqa: N802 - tên do BaseHTTPRequestHandler quy định
        query = parse_qs(urlparse(self.path).query)
        self.server.oauth_result = query

        if "code" in query and "error" not in query:
            body = _callback_page(
                "Đã nhận quyền YouTube.",
                "Bạn có thể đóng tab này và quay lại cửa sổ terminal.",
            )
            status = 200
        else:
            detail = (query.get("error_description") or query.get("error") or ["?"])[0]
            body = _callback_page("Không nhận được quyền.", str(detail))
            status = 400

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # pragma: no cover - không log request loopback
        pass


class _CallbackServer(HTTPServer):
    """HTTPServer có chỗ lưu kết quả callback."""

    oauth_result = None
    allow_reuse_address = True


def _callback_page(title, message):
    html = (
        "<!doctype html><html lang='vi'><meta charset='utf-8'>"
        "<title>YouTube OAuth</title>"
        "<body style='font-family:sans-serif;max-width:40rem;margin:4rem auto'>"
        f"<h1>{title}</h1><p>{message}</p></body></html>"
    )
    return html.encode("utf-8")


def looks_like_refresh_token(value):
    """Google refresh token luôn bắt đầu bằng ``1/`` và khá dài."""
    return isinstance(value, str) and value.startswith("1/") and len(value) >= 30


def load_client_config(path=None, environ=None):
    """Đọc client id/secret từ file client secret hoặc từ biến môi trường.

    Returns:
        (client_id, client_secret, nguồn) — ``nguồn`` chỉ để in cho người dùng.
    """
    environ = os.environ if environ is None else environ

    if path:
        candidate = Path(path)

        if not candidate.is_file():
            raise ConfigurationError(
                f"Không thấy file client secret: {candidate}"
            )

        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ConfigurationError(
                f"Không đọc được {candidate}: {type(error).__name__}."
            ) from error

        block = None

        if isinstance(data, dict):
            block = data.get("installed") or data.get("web")

        if not isinstance(block, dict):
            raise ConfigurationError(
                f"{candidate} không phải client secret của Google "
                "(thiếu object 'installed'/'web')."
            )

        client_id = str(block.get("client_id") or "").strip()
        client_secret = str(block.get("client_secret") or "").strip()

        if not client_id or not client_secret:
            raise ConfigurationError(
                f"{candidate} thiếu client_id hoặc client_secret."
            )

        client_type = "installed" if data.get("installed") else "web"

        if client_type != "installed":
            raise ConfigurationError(
                "Client secret này là loại 'Web application'. Hãy tạo OAuth "
                "client loại 'Desktop app' để dùng luồng loopback."
            )

        return client_id, client_secret, str(candidate)

    client_id = str(environ.get("YT_CLIENT_ID") or "").strip()
    client_secret = str(environ.get("YT_CLIENT_SECRET") or "").strip()

    if client_id and client_secret:
        return client_id, client_secret, "biến môi trường (.env)"

    raise ConfigurationError(
        "Không tìm thấy OAuth client. Đặt yt-secret/client_secret.json hoặc "
        "điền YT_CLIENT_ID + YT_CLIENT_SECRET trong .env."
    )


def build_auth_url(client_id, redirect_uri, scopes=None, state=None):
    """URL đồng ý của Google; ``prompt=consent`` để luôn trả refresh token."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes or DEFAULT_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }

    if state:
        params["state"] = state

    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(session, client_id, client_secret, code, redirect_uri,
                  timeout=30):
    """Đổi authorization code -> access token + refresh token."""
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        response = session.post(TOKEN_URL, data=data, timeout=timeout)
    except requests.RequestException as error:
        raise ExternalServiceError(
            f"Không gọi được {TOKEN_URL}: {type(error).__name__}.",
            service="youtube",
            retryable=True,
        ) from error

    payload = _json_payload(response)

    if response.status_code >= 400:
        raise ExternalServiceError(
            "Google từ chối đổi authorization code: "
            f"{_error_detail(payload)} (HTTP {response.status_code}).",
            service="youtube",
            status=response.status_code,
            retryable=is_transient_status(response.status_code),
        )

    if not payload.get("access_token"):
        raise ExternalServiceError(
            "Google không trả access_token khi đổi authorization code.",
            service="youtube",
        )

    if not payload.get("refresh_token"):
        raise ConfigurationError(
            "Google không trả refresh_token (thường do app đã được cấp quyền "
            "trước đó). Vào https://myaccount.google.com/permissions thu hồi "
            "quyền của app rồi chạy lại script này."
        )

    return payload


def run_oauth_flow(client_id, client_secret, scopes=None, port=0,
                   timeout=DEFAULT_TIMEOUT_SECONDS, open_url=True, session=None,
                   output=None):
    """Mở trình duyệt, chờ callback loopback, trả payload token của Google."""
    output = output or Console.print
    session = session or requests.Session()
    server = _CallbackServer(("localhost", int(port)), _OAuthCallbackHandler)
    actual_port = server.server_address[1]
    redirect_uri = f"http://localhost:{actual_port}"
    state = secrets.token_urlsafe(16)
    url = build_auth_url(client_id, redirect_uri, scopes=scopes, state=state)

    output(f"Redirect URI: {redirect_uri}")
    output("Đang mở trình duyệt để cấp quyền YouTube...")

    if callable(open_url):
        try:
            open_url(url)
        except Exception:  # pragma: no cover - lỗi trình duyệt thì in URL
            pass
    elif open_url:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - không có trình duyệt thì in URL
            pass

    output("Nếu trình duyệt không tự mở, dán URL sau vào trình duyệt:")
    output(url)

    try:
        deadline = time.monotonic() + max(1.0, float(timeout))

        while server.oauth_result is None:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                raise ConfigurationError(
                    f"Hết {int(timeout)} giây mà chưa nhận được callback từ "
                    "Google. Chạy lại script và hoàn tất đồng ý trong trình duyệt."
                )

            server.timeout = min(1.0, remaining)
            server.handle_request()
    finally:
        server.server_close()

    result = server.oauth_result or {}
    error = (result.get("error") or [None])[0]

    if error:
        detail = (result.get("error_description") or [error])[0]
        raise ConfigurationError(
            f"Google trả lỗi khi cấp quyền: {detail}"
        )

    received_state = (result.get("state") or [None])[0]

    if received_state != state:
        raise ConfigurationError(
            "State trong callback không khớp (có thể bị chặn/đổi URL). "
            "Chạy lại script."
        )

    code = (result.get("code") or [None])[0]

    if not code:
        raise ConfigurationError("Callback không có authorization code.")

    return exchange_code(
        session, client_id, client_secret, code, redirect_uri
    )


def save_token(path, payload, client_id, scopes=None):
    """Ghi ``refresh_token`` + scope ra file JSON (không ghi client secret)."""
    refresh_token = payload.get("refresh_token")

    if not refresh_token:
        raise ConfigurationError("Payload token thiếu refresh_token.")

    granted = payload.get("scope") or " ".join(scopes or DEFAULT_SCOPES)
    expires_in = payload.get("expires_in")
    expires_at = None

    try:
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=float(expires_in or 0))
        ).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        expires_at = None

    data = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "scopes": granted.split(),
        "obtained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "access_token_expires_at": expires_at,
    }

    path = Path(path)
    _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=4) + "\n")
    return path


def read_refresh_token(path):
    """Đọc refresh token từ file token (dùng cho PublishService nếu cần)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    token = data.get("refresh_token") if isinstance(data, dict) else None

    if not isinstance(token, str) or not token.strip():
        raise ConfigurationError(
            f"{path} không chứa refresh_token hợp lệ."
        )

    return token.strip()


def update_env_file(path, values):
    """Cập nhật các khoá trong .env, giữ nguyên comment và dòng khác."""
    for key, value in values.items():
        if "\n" in str(value) or "\r" in str(value):
            raise ConfigurationError(
                f"Giá trị của {key} không được chứa ký tự xuống dòng."
            )

    path = Path(path)

    try:
        # newline="" để giữ nguyên kiểu xuống dòng (CRLF của Windows).
        with open(path, "r", encoding="utf-8", newline="") as file:
            text = file.read()
    except FileNotFoundError:
        text = ""

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pending = {key: str(value) for key, value in values.items()}

    for index, line in enumerate(lines):
        match = _ENV_LINE.match(line)

        if not match or match.group("key") not in pending:
            continue

        key = match.group("key")
        lines[index] = f"{match.group('prefix')}{key}={pending.pop(key)}"

    for key, value in pending.items():
        lines.append(f"{key}={value}")

    updated = newline.join(lines)

    if updated:
        updated += newline

    _write_atomic(path, updated)
    return path


def _json_payload(response):
    try:
        payload = response.json()
    except ValueError as error:
        raise ExternalServiceError(
            "Phản hồi Google không phải JSON hợp lệ.",
            service="youtube",
        ) from error

    if not isinstance(payload, dict):
        raise ExternalServiceError(
            "Phản hồi Google không phải object JSON.",
            service="youtube",
        )

    return payload


def _error_detail(payload):
    error = payload.get("error")
    description = str(payload.get("error_description") or "").strip()

    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        return f"{message}: {description}" if description else message

    message = str(error or "").strip()
    return f"{message}: {description}" if description else (description or message)


def _write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.part")

    try:
        with open(temp, "w", encoding="utf-8", newline="") as file:
            file.write(text)

        temp.replace(path)
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
        raise

    return path


def _existing_refresh_token(token_file, environ):
    try:
        token = read_refresh_token(token_file)
    except (OSError, ValueError, ConfigurationError):
        token = None

    if looks_like_refresh_token(token):
        return f"trong {token_file}"

    if looks_like_refresh_token(environ.get("YT_REFRESH_TOKEN")):
        return "trong .env"

    return None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Lấy refresh token YouTube (OAuth2) để PublishService đăng video. "
            "Chạy một lần trên máy có trình duyệt."
        )
    )
    parser.add_argument(
        "--client-secret",
        default=None,
        help=f"File client secret của OAuth Desktop app (mặc định: {DEFAULT_CLIENT_SECRET}).",
    )
    parser.add_argument(
        "--token-file",
        default=str(DEFAULT_TOKEN_FILE),
        help=f"Nơi ghi token (mặc định: {DEFAULT_TOKEN_FILE}).",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help=f"File .env cần cập nhật (mặc định: {DEFAULT_ENV_FILE}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Cổng loopback; 0 = chọn cổng trống tự động (khuyến nghị).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Giây chờ callback từ Google (mặc định: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Không tự mở trình duyệt; chỉ in URL.",
    )
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="Không cập nhật .env (chỉ ghi token file).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Lấy token mới dù đã có refresh token.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)

        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()
    args = parse_args(argv)

    try:
        client_path = (
            Path(args.client_secret) if args.client_secret
            else DEFAULT_CLIENT_SECRET
        )

        if args.client_secret and not client_path.is_file():
            raise ConfigurationError(
                f"Không thấy file client secret: {client_path}"
            )

        client_id, client_secret, source = load_client_config(
            client_path if client_path.is_file() else None
        )

        existing = _existing_refresh_token(args.token_file, os.environ)

        if existing and not args.force:
            Console.info(
                f"Đã có refresh token {existing}. Dùng --force để lấy token mới."
            )
            return 0

        Console.info(f"OAuth client: {source}")

        payload = run_oauth_flow(
            client_id,
            client_secret,
            port=args.port,
            timeout=args.timeout,
            open_url=not args.no_browser,
        )

        token_file = save_token(args.token_file, payload, client_id)
        Console.success(f"Đã lưu refresh token vào {token_file}.")

        if not args.no_env:
            update_env_file(
                args.env_file,
                {
                    "YT_CLIENT_ID": client_id,
                    "YT_CLIENT_SECRET": client_secret,
                    "YT_REFRESH_TOKEN": payload["refresh_token"],
                },
            )
            Console.success(f"Đã cập nhật {args.env_file}.")

        Console.print(
            "Xong. Chạy lại pipeline (main.py) để đăng video lên YouTube."
        )
        return 0
    except AppError as error:
        Console.error(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
