import requests
from bs4 import BeautifulSoup


class ResearchAgent:
    # get link -> extract link from RSS feed
    def __init__(self):
        pass

    def GetLink(self, urlRss):
        response = requests.get(urlRss)

        soup = BeautifulSoup(response.text, "xml")

        article = soup.find("item")

        if article:
            link = article.find("link")

            if link:
                return link.get_text(strip=True)

        return None

    def VnexpressExtractor(self,link):

        response = requests.get(link,timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        article = soup.find("article", class_="fck_detail")

        title = article.find("h1", class_="title-detail")

        description = article.find("p", class_="description")

        paragraphs = (
            article.find_all("p", class_="Normal")
            if article else []
        )

        return {
            "title": title.get_text(" ", strip=True) if title else "",
            "description": description.get_text(" ", strip=True) if description else "",
            "content": "\n".join(
                p.get_text(" ", strip=True)
                for p in paragraphs
            )
        }

    def Search(self, urlRss):
        link = self.GetLink(urlRss)

        if not link:
            return None

        return self.VnexpressExtractor(link)
