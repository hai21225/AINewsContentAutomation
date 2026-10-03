
import random

class TopicSelector:

    def __init__(self, rssConfig):
        self._rssConfig = rssConfig

    def select(self,amount=5):
        topics = list(self._rssConfig.items())

        selected = random.sample(topics, min(amount, len(topics)))

        return [
            {
                "topic": topic,
                "url": rssUrl
            }
            for topic, rssUrl in selected
        ]

    def _is_relevant(self, article):
        # Kiểm tra xem bài viết có phù hợp hay không
        return True  # Placeholder cho logic thực tế