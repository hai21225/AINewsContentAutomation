
from Agents.ResearchAgent import ResearchAgent
from Agents.WriterAgent import WriterAgent
from Config.ConfigLoader import ConfigLoader
from LLM.AIClient import AiClient

from Services.TopicSelector import TopicSelector


configloader = ConfigLoader("config.json")


agentresearch = ResearchAgent()

writerConfig = configloader.GetAgentConfig("writer")

rssconfig= configloader.GetRssConfig()

llmconfig = configloader.GetLlmConfig()

llmClient = AiClient(llmconfig)


article = agentresearch.Search(rssconfig["thoi_su"])
writerAgent = WriterAgent(writerConfig, llmClient)


result = writerAgent.run(article)

print(result["title"])

print(result["tts_text"])

print(result["hashtags"])


word_count = len(result["tts_text"].split())

print("Số từ:", word_count)

# selectorTopic = TopicSelector(rssconfig)

# selectorTopics = selectorTopic.select(1)

# for topic in selectorTopics:
#     print(topic)
