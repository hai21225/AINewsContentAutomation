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

    def _ExtractImages(self, article):
        images = []

        figures = article.find_all("figure")

        for figure in figures:

            # Ưu tiên link ảnh chất lượng cao
            image_url = None

            fig_picture = figure.find(
                "div",
                class_="fig-picture"
            )

            if fig_picture:
                image_url = fig_picture.get("data-src")

            # fallback 1
            if not image_url:
                meta = figure.find(
                    "meta",
                    attrs={"itemprop": "url"}
                )

                if meta:
                    image_url = meta.get("content")

            # fallback 2
            if not image_url:
                img = figure.find("img")

                if img:
                    image_url = (
                        img.get("data-src")
                        or img.get("src")
                    )

            if not image_url:
                continue

            # Lấy alt của ảnh
            img = figure.find("img")

            alt = ""
            if img:
                alt = img.get("alt", "").strip()

            # Lấy caption
            figcaption = figure.find("figcaption")

            caption = ""
            if figcaption:
                caption = figcaption.get_text(
                    " ",
                    strip=True
                )

            images.append({
                "url": image_url,
                "alt": alt,
                "caption": caption
            })

        return images

    def VnexpressExtractor(self, link):
        response = requests.get(link, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        article = soup.find(
            "article",
            class_="fck_detail"
        )

        title = (
            article.find("h1", class_="title-detail")
            if article
            else None
        )

        description = (
            article.find("p", class_="description")
            if article
            else None
        )

        paragraphs = (
            article.find_all("p", class_="Normal")
            if article
            else []
        )

        images = (
            self._ExtractImages(article)
            if article
            else []
        )

        return {
            "id": sha256(
                link.encode("utf-8")
            ).hexdigest()[:16],

            "title": (
                title.get_text(" ", strip=True)
                if title
                else ""
            ),

            "des": (
                description.get_text(" ", strip=True)
                if description
                else ""
            ),

            "content": "\n".join(
                p.get_text(" ", strip=True)
                for p in paragraphs
            ),

            "images": images,

            "source_url": link
        }

    def Search(self, urlRss):
        """Lấy đúng bài đầu tiên từ một RSS."""

        link = self.GetLink(urlRss)

        if not link:
            return None

        article = self.VnexpressExtractor(link)

        return (
            article
            if article["title"]
            and article["content"]
            else None
        )

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