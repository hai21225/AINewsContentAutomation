from Agents.ResearchAgent import ResearchAgent
from Agents.WriterAgent import WriterAgent
from Agents.CheckerAgent import CheckerAgent
from Agents.SuperVisorAgent import SuperVisorAgent

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
# CREATE AGENTS
# =========================

research_agent = ResearchAgent()

writer_agent = WriterAgent(
    writer_config,
    writer_llm_client
)

checker_agent = CheckerAgent(
    checker_config,
    checker_llm_client
)

supervisor_agent = SuperVisorAgent(
    writer_agent,
    checker_agent
)


# =========================
# TEST
# =========================

article = research_agent.Search(
    rss_config["thoi_su"]
)

result = supervisor_agent.run(article)


# =========================
# OUTPUT
# =========================

print("\n========== RESULT ==========\n")

print("Title:")
print(result["title"])

print("\nTTS:")
print(result["tts_text"])

print("\nHashtags:")
print(result["hashtags"])

word_count = len(result["tts_text"].split())

print("\nSố từ:", word_count)