import requests
from bs4 import BeautifulSoup




class ResearchAgent:

    _url_rss = None
    _link = None

    def __init__(self,url,link):
        self._url_rss = url
        self._link = link

    def search(self):
        response = requests.get(self._url_rss)
        #extract link
        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.find("item")
        if articles:
            link = articles.find("link").get_text()
        return link

    def VnexpressExtractor(self):
        response = requests.get(self._url_rss)
        soup = BeautifulSoup(response.text, "html.parser")

        article = soup.find("article", class_="fck_detail")

        title = article.find("h1", class_="title-detail")

        description = article.find("p", class_="description")

        paragraphs = article.find_all("p", class_="Normal")

        return {
            "title": title.get_text(" ", strip=True) if title else "",
            "description": description.get_text(" ", strip=True) if description else "",
            "content": "\n".join(
                p.get_text(" ", strip=True)
                for p in paragraphs
            )
        }
