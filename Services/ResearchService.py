import requests
from bs4 import BeautifulSoup
from hashlib import sha256

class ResearchService:
    """Lấy bài viết đầu tiên của mỗi RSS category và trích xuất nội dung."""

    def GetLink(self, urlRss):
        response = requests.get(urlRss, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "xml")
        item = soup.find("item")
        link = item.find("link") if item else None
        return link.get_text(strip=True) if link else None

    def VnexpressExtractor(self, link):
        response = requests.get(link, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        article = soup.find("article", class_="fck_detail")
        title = article.find("h1", class_="title-detail") if article else None
        description = article.find("p", class_="description") if article else None
        paragraphs = article.find_all("p", class_="Normal") if article else []

        return {
            "id": sha256(link.encode("utf-8")).hexdigest()[:16],
            "title": title.get_text(" ", strip=True) if title else "",
            "des": description.get_text(" ", strip=True) if description else "",
            "content": "\n".join(p.get_text(" ", strip=True) for p in paragraphs),
        }

    def Search(self, urlRss):
        """Lấy đúng bài đầu tiên từ một RSS."""
        link = self.GetLink(urlRss)
        if not link:
            return None

        article = self.VnexpressExtractor(link)
        return article if article["title"] and article["content"] else None

    def SearchAll(self, rss_config):
        """Mỗi category chỉ lấy một bài đầu tiên."""
        articles = []
        for category, url_rss in rss_config.items():
            try:
                article = self.Search(url_rss)
                if article:
                    articles.append(article)
            except requests.RequestException as error:
                raise RuntimeError(
                    f"Không thể lấy RSS {category}: {error}"
                ) from error
        return articles
