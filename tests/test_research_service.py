import pytest
import requests

from Errors import ExternalServiceError
from Services.ResearchService import ResearchService
from fakes import FakeHttpResponse, FakeSession

RSS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>Tin</title>'
    "<item><title>Bài A</title><link>https://example.com/a</link></item>"
    "<item><title>Bài B</title><link>https://example.com/b</link></item>"
    "</channel></rss>"
)

ATOM_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    "<entry><title>A</title>"
    '<link href="https://example.com/atom"/></entry></feed>'
)

ARTICLE_HTML = """
<html><body>
<article class="fck_detail">
  <h1 class="title-detail">Tiêu đề bài</h1>
  <p class="description">Mô tả bài viết</p>
  <p class="Normal">Đoạn một.</p>
  <p class="Normal">Đoạn hai.</p>
  <figure>
    <div class="fig-picture" data-src="https://img.example.com/1.jpg"></div>
    <img alt="Ảnh minh hoạ" src="https://img.example.com/1.jpg">
    <figcaption>Chú thích ảnh</figcaption>
  </figure>
</article>
</body></html>
"""

EMPTY_ARTICLE_HTML = "<html><body><div>Không có bài</div></body></html>"


def rss_response(xml=RSS_XML):
    return FakeHttpResponse(200, xml, content=xml.encode("utf-8"))


def rss_with_link(link):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        f"<item><title>Bài</title><link>{link}</link></item>"
        "</channel></rss>"
    )
    return rss_response(xml)


def article_response(html=ARTICLE_HTML):
    return FakeHttpResponse(200, html, content=html.encode("utf-8"))


def build_service(results, **config):
    session = FakeSession(results)
    service = ResearchService(config, session=session, sleep=lambda seconds: None)
    return service, session


# ----------------------------------------------------------------------
# get_link
# ----------------------------------------------------------------------


def test_get_link_reads_rss_2_0():
    service, session = build_service([rss_response()])

    assert service.get_link("https://example.com/rss") == "https://example.com/a"
    assert session.requests[0]["timeout"] == 10


def test_get_link_reads_atom_entry():
    service, _ = build_service([rss_response(ATOM_XML)])

    assert service.get_link("https://example.com/rss") == "https://example.com/atom"


def test_get_link_without_items_returns_none():
    xml = '<?xml version="1.0"?><rss><channel></channel></rss>'
    service, _ = build_service([rss_response(xml)])

    assert service.get_link("https://example.com/rss") is None


def test_invalid_xml_is_permanent_error():
    service, session = build_service(
        [FakeHttpResponse(200, "<html>khong-phai-rss", content=b"<html>x")]
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        service.get_link("https://example.com/rss")

    assert excinfo.value.retryable is False
    assert len(session.requests) == 1


# ----------------------------------------------------------------------
# Retry / timeout
# ----------------------------------------------------------------------


def test_retries_transient_error_then_succeeds():
    service, session = build_service(
        [FakeHttpResponse(503, "loi"), rss_response()],
        max_attempts=3,
    )

    assert service.get_link("https://example.com/rss") == "https://example.com/a"
    assert len(session.requests) == 2


def test_permanent_http_error_is_not_retried():
    service, session = build_service(
        [FakeHttpResponse(404, "khong thay")], max_attempts=3
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        service.get_link("https://example.com/rss")

    assert excinfo.value.retryable is False
    assert excinfo.value.status == 404
    assert len(session.requests) == 1


def test_timeout_is_retried_until_exhausted():
    service, session = build_service(
        [requests.Timeout("cham"), requests.Timeout("cham")], max_attempts=2
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        service.get_link("https://example.com/rss")

    assert excinfo.value.retryable is True
    assert len(session.requests) == 2


def test_retry_after_header_is_honoured():
    slept = []

    session = FakeSession(
        [
            FakeHttpResponse(429, "qua tai", headers={"Retry-After": "2"}),
            rss_response(),
        ]
    )
    service = ResearchService(
        {"max_attempts": 3}, session=session, sleep=slept.append
    )

    assert service.get_link("https://example.com/rss") == "https://example.com/a"
    assert slept == [2.0]


# ----------------------------------------------------------------------
# extract & search
# ----------------------------------------------------------------------


def test_extract_vnexpress_parses_article():
    service, _ = build_service([article_response()])

    article = service.extract_vnexpress("https://example.com/a")

    assert article["title"] == "Tiêu đề bài"
    assert article["des"] == "Mô tả bài viết"
    assert "Đoạn một." in article["content"]
    assert article["source_url"] == "https://example.com/a"
    assert len(article["id"]) == 16
    assert article["images"] == [
        {
            "url": "https://img.example.com/1.jpg",
            "alt": "Ảnh minh hoạ",
            "caption": "Chú thích ảnh",
        }
    ]


def test_search_returns_none_when_article_incomplete():
    service, _ = build_service(
        [rss_response(), article_response(EMPTY_ARTICLE_HTML)]
    )

    assert service.search("https://example.com/rss") is None


# ----------------------------------------------------------------------
# search_all
# ----------------------------------------------------------------------


def test_search_all_isolates_failed_feed():
    service, session = build_service(
        [
            rss_with_link("https://example.com/a"),
            article_response(),
            FakeHttpResponse(500, "loi"),
            FakeHttpResponse(500, "loi"),
            FakeHttpResponse(500, "loi"),
            rss_with_link("https://example.com/c"),
            article_response(),
        ],
        max_attempts=3,
    )

    articles = service.search_all(
        {
            "feed_1": "https://example.com/rss-1",
            "feed_2": "https://example.com/rss-2",
            "feed_3": "https://example.com/rss-3",
        }
    )

    assert len(articles) == 2
    assert [a["category"] for a in articles] == ["feed_1", "feed_3"]
    assert len(service.last_failures) == 1
    assert service.last_failures[0]["category"] == "feed_2"


def test_search_all_deduplicates_same_article():
    service, _ = build_service(
        [
            rss_response(),
            article_response(),
            rss_response(),
            article_response(),
        ]
    )

    articles = service.search_all(
        {"feed_1": "https://example.com/rss-1", "feed_2": "https://example.com/rss-2"}
    )

    assert len(articles) == 1


def test_search_all_with_missing_item_is_skipped():
    xml = '<?xml version="1.0"?><rss><channel></channel></rss>'
    service, _ = build_service([rss_response(xml)])

    articles = service.search_all({"feed_1": "https://example.com/rss"})

    assert articles == []
    assert service.last_failures[0]["category"] == "feed_1"


def test_search_all_without_feeds_returns_empty():
    service, _ = build_service([])

    assert service.search_all({}) == []
