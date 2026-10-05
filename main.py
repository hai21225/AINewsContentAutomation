import json
from Agents.Workflow import Workflow
from Agents.WriterAgent import WriterAgent
from Agents.CheckerAgent import CheckerAgent
from Agents.WriterSuperVisor import WriterSuperVisor
from Agents.SelectorAgent import SelectorAgent
from Services.ResearchService import ResearchService
from Services.TtsService import TtsService

from Config.ConfigLoader import ConfigLoader
from LLM.AiClientFactory import AiClientFactory

from dotenv import load_dotenv
load_dotenv()

config_loader = ConfigLoader()

# =========================
# LOAD CONFIG
# =========================

models = config_loader.get_models()
providers = config_loader.get_providers()
rss_config = config_loader.get_rss()

writer_config = config_loader.get_agent("writer")
checker_config = config_loader.get_agent("checker")
selector_config = config_loader.get_agent("selector")
tts_config = config_loader.get_tts()


# =========================
# CREATE WRITER CLIENT
# =========================

writer_model_name = writer_config["model"]

writer_model_config = models[writer_model_name]

writer_provider_name = writer_model_config["provider"]

writer_provider_config = providers[writer_provider_name]

writer_llm_client = AiClientFactory.create(
    writer_provider_config,
    writer_model_config
)


# =========================
# CREATE CHECKER CLIENT
# =========================

checker_model_name = checker_config["model"]

checker_model_config = models[checker_model_name]

checker_provider_name = checker_model_config["provider"]

checker_provider_config = providers[checker_provider_name]

checker_llm_client = AiClientFactory.create(
    checker_provider_config,
    checker_model_config
)

# =========================
# CREATE SELECTOR CLIENT
# =========================

selector_model_name = selector_config["model"]
selector_model_config = models[selector_model_name]
selector_provider_name = selector_model_config["provider"]
selector_provider_config = providers[selector_provider_name]

selector_llm_client = AiClientFactory.create(
    selector_provider_config,
    selector_model_config
)


# =========================
# CREATE AGENTS
# =========================

research_service = ResearchService()

writer_agent = WriterAgent(
    writer_config,
    writer_llm_client
)

checker_agent = CheckerAgent(
    checker_config,
    checker_llm_client
)

selector_agent = SelectorAgent(
    selector_config,
    selector_llm_client
)

tts_service = TtsService(tts_config)

supervisor_agent = WriterSuperVisor(
    writer_agent,
    checker_agent
)



workflow = Workflow(
    research_service,
    selector_agent,
    supervisor_agent,
    ttsService=tts_service
)

print(workflow.run(rss_config))

# =========================
# TEST
# =========================

# articles = research_service.SearchAll(rss_config)
# selected = selector_agent.run(articles)

# selected_ids = {item["id"] for item in selected}
# selected_articles = [
#     article for article in articles if article["id"] in selected_ids
# ]

# print("\n========== SELECTED ARTICLES ==========")
# print(json.dumps(selected, ensure_ascii=False, indent=2))

# if not selected_articles:
#     raise ValueError("SelectorAgent không chọn được bài báo nào.")

# article = selected_articles[0]

# result = supervisor_agent.run(article)


# # =========================
# # OUTPUT
# # =========================

# print("\n========== RESULT ==========\n")

# print("Title:")
# print(result["title"])

# print("\nTTS:")
# print(result["tts_text"])

# print("\nHashtags:")
# print(result["hashtags"])

# word_count = len(result["tts_text"].split())

# print("\nSố từ:", word_count)
