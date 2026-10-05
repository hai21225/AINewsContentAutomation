from Console import Console



class Workflow: 

    def __init__(self, researchService, selectorAgent, writingSupervisor, ttsService =None, videoService= None, socialService=None):
        self.researchService = researchService
        self.selectorAgent = selectorAgent
        self.writingSupervisor = writingSupervisor
        self.ttsService = ttsService
        self.videoService = videoService
        self.socialService = socialService

    def run(self, rssConfig):
        try:
            Console.loading("Đang lấy dữ liệu RSS")
            articles = self.researchService.SearchAll(rssConfig)
            Console.success(f"Đã lấy {len(articles)} bài viết")

            Console.loading("Đang chọn bài viết")
            selected = self.selectorAgent.run(articles)
            Console.success(f"Đã chọn {len(selected)} bài viết")

            selectedIds = {
                x["id"]
                for x in selected
            }

            articles = [
                article
                for article in articles
                if article["id"] in selectedIds
            ]

            results = []

            for index, article in enumerate(articles, start=1):
                results.append(self.process_article(article, index, len(articles)))

            Console.success("Đã hoàn tất toàn bộ workflow")
            return results

        except Exception as error:
            Console.error(f"Workflow thất bại: {error}")
            raise

    def process_article(self, article, index, total):
        Console.loading(
            f"Đang viết bài {index}/{total}: {article['title'][:45]}"
        )
        content = self.writingSupervisor.run(
            article
        )
        Console.success(f"Đã viết bài {index}/{total}")

        result = {
            "article_id": article["id"],
            "content": content
        }

        if self.ttsService:
            Console.loading(
                f"Đang tạo audio {index}/{total}"
            )
            result["audio"] = self.ttsService.run(
                article["id"],
                content["tts_text"]
            )
            Console.success(
                f"Đã tạo audio {index}/{total}: {result['audio']}"
            )
        # if self.videoAgent:
        #     result["video"] = self.videoAgent.run(
        #         result
        #     )

        # if self.socialAgent:
        #     result["social"] = self.socialAgent.run(
        #         result
        #     )

        return result
