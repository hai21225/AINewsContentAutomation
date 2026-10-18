import xml.etree.ElementTree as ElementTree
from hashlib import sha256

import requests
from bs4 import BeautifulSoup

from Errors import ExternalServiceError, is_transient_status
from Retry import RetryPolicy, retry_after_seconds

DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.5
DEFAULT_MAX_BACKOFF_SECONDS = 8.0
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; AINewsContentAutomation/1.0)"


class ResearchService:
    """Thu thập bài viết từ RSS và trích xuất nội dung (layout VnExpress).

    Đặc tính quan trọng: một feed lỗi **không** làm hỏng cả bước thu thập.
    Feed lỗi được ghi vào ``last_failures`` và chỉ bị bỏ qua.
    """

    def __init__(self, config=None, log_service=None, session=None, sleep=None,
                 retry_config=None):
        config = config or {}

        self._timeout = int(
            config.get("request_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": config.get("user_agent", DEFAULT_USER_AGENT)}
        )

        self._log = log_service

        # Chính sách retry dùng chung; cấu hình của mục "research" ghi đè
        # giá trị mặc định, còn retry_config (nếu có) là mức chung toàn pipeline.
        self._retry = RetryPolicy.from_config(
            {
                "max_attempts": DEFAULT_MAX_ATTEMPTS,
                "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
                "max_backoff_seconds": DEFAULT_MAX_BACKOFF_SECONDS,
            },
            retry_config,
            config,
            sleep=sleep,
        )

        #: Danh sách feed bị bỏ qua ở lần chạy gần nhất.
        self.last_failures = []

    @property
    def retry_policy(self):
        return self._retry

    # ------------------------------------------------------------------
    # Điểm vào
    # ------------------------------------------------------------------

    def search_all(self, rss_config):
        """Lấy bài đầu tiên của mỗi RSS; feed lỗi bị bỏ qua, không raise."""
        articles = []
        self.last_failures = []
        seen_ids = set()
        duplicates = 0

        for category, url_rss in rss_config.items():
            try:
                article = self.search(url_rss)
            except ExternalServiceError as error:
                self._record_failure(category, str(error))
                continue
            except Exception as error:  # phòng lỗi parse bất ngờ của 1 feed
                self._record_failure(
                    category, f"{type(error).__name__}: {error}"
                )
                continue

            if not article:
                self._record_failure(
                    category, "Không trích xuất được tiêu đề/nội dung."
                )
                continue

            if article["id"] in seen_ids:
                duplicates += 1
                self._log_warning(
                    "rss_duplicate_article",
                    "Bỏ qua bài trùng giữa các feed.",
                    category=category,
                    article_id=article["id"],
                )
                continue

            seen_ids.add(article["id"])
            article["category"] = category
            articles.append(article)

        self._log_info(
            "rss_collected",
            f"Thu thập {len(articles)}/{len(rss_config)} feed thành công.",
            collected=len(articles),
            total_feeds=len(rss_config),
            skipped=len(self.last_failures),
            duplicates=duplicates,
        )

        return articles

    def search(self, url_rss):
        """Lấy bài đầu tiên của một RSS; trả None nếu không có bài hợp lệ."""
        link = self.get_link(url_rss)

        if not link:
            return None

        article = self.extract_vnexpress(link)

        if article["title"] and article["content"]:
            return article

        return None

    def get_link(self, url_rss):
        """Lấy link bài viết đầu tiên trong RSS (RSS 2.0 hoặc Atom).

        Dùng ``xml.etree`` của thư viện chuẩn thay vì parser ``xml`` của
        BeautifulSoup: parser đó cần ``lxml`` (một phụ thuộc chưa từng được
        khai báo trong dự án) và sẽ lỗi ``FeatureNotFound`` ở môi trường mới.
        """
        response = self._get_response(url_rss, service="rss")

        return self._first_item_link(response.content)

    @staticmethod
    def _first_item_link(raw_xml):
        """Trả về link của phần tử item/entry đầu tiên, None nếu không có."""
        if not raw_xml:
            raise ExternalServiceError(
                "RSS trả về nội dung rỗng.",
                service="rss",
                retryable=True,
            )

        try:
            root = ElementTree.fromstring(raw_xml)
        except ElementTree.ParseError as error:
            raise ExternalServiceError(
                f"RSS không phải XML hợp lệ: {error}",
                service="rss",
                retryable=False,
            ) from error

        for element in root.iter():
            if ResearchService._local_name(element.tag) not in ("item", "entry"):
                continue

            for child in element:
                if ResearchService._local_name(child.tag) != "link":
                    continue

                link = (child.text or "").strip()

                if link:
                    return link

                href = (child.get("href") or "").strip()

                if href:
                    return href

        return None

    @staticmethod
    def _local_name(tag):
        """Bỏ namespace khỏi tên thẻ XML (``{ns}link`` -> ``link``)."""
        if isinstance(tag, str) and "}" in tag:
            return tag.rsplit("}", 1)[1]

        return tag

    def extract_vnexpress(self, link):
        """Trích xuất tiêu đề/mô tả/nội dung/ảnh từ một bài VnExpress."""
        response = self._get_response(link, service="article")

        soup = BeautifulSoup(response.text, "html.parser")

        article = soup.find("article", class_="fck_detail")

        title = article.find("h1", class_="title-detail") if article else None
        description = (
            article.find("p", class_="description") if article else None
        )
        paragraphs = (
            article.find_all("p", class_="Normal") if article else []
        )
        images = self._extract_images(article) if article else []

        return {
            "id": sha256(link.encode("utf-8")).hexdigest()[:16],
            "title": title.get_text(" ", strip=True) if title else "",
            "des": description.get_text(" ", strip=True) if description else "",
            "content": "\n".join(
                paragraph.get_text(" ", strip=True) for paragraph in paragraphs
            ),
            "images": images,
            "source_url": link,
        }

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    def _extract_images(self, article):
        images = []

        for figure in article.find_all("figure"):
            image_url = None

            fig_picture = figure.find("div", class_="fig-picture")

            if fig_picture:
                image_url = fig_picture.get("data-src")

            if not image_url:
                meta = figure.find("meta", attrs={"itemprop": "url"})

                if meta:
                    image_url = meta.get("content")

            if not image_url:
                img = figure.find("img")
                if img:
                    image_url = img.get("data-src") or img.get("src")

            if not image_url:
                continue

            img = figure.find("img")
            alt = img.get("alt", "").strip() if img else ""

            figcaption = figure.find("figcaption")
            caption = (
                figcaption.get_text(" ", strip=True) if figcaption else ""
            )

            images.append(
                {"url": image_url, "alt": alt, "caption": caption}
            )

        return images

    def _get_response(self, url, service):
        """GET có retry cho lỗi tạm thời; tôn trọng Retry-After."""
        return self._retry.run(
            lambda: self._request_once(url, service),
            event_name="rss_retry",
            description=url,
            service=service,
            log=self._log,
        )

    def _request_once(self, url, service):
        """Một lần GET; raise ExternalServiceError có cờ retryable khi lỗi."""
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as error:
            raise ExternalServiceError(
                f"Không gọi được {url}: {type(error).__name__}.",
                service=service,
                retryable=True,
            ) from error

        if response.status_code < 400:
            return response

        raise ExternalServiceError(
            f"HTTP {response.status_code} khi gọi {url}.",
            service=service,
            status=response.status_code,
            retryable=is_transient_status(response.status_code),
            retry_after=retry_after_seconds(response.headers),
        )

    def _record_failure(self, category, reason):
        self.last_failures.append({"category": category, "error": reason})
        self._log_error(
            "rss_feed_failed",
            f"Bỏ qua feed '{category}': {reason}",
            category=category,
            error=reason,
        )

    def _log_info(self, event, message="", **fields):
        if self._log is not None:
            self._log.info(event, message, **fields)

    def _log_warning(self, event, message="", **fields):
        if self._log is not None:
            self._log.warning(event, message, **fields)

    def _log_error(self, event, message="", **fields):
        if self._log is not None:
            self._log.error(event, message, **fields)
