import json
from Agents.Workflow import Workflow
from Agents.WriterAgent import WriterAgent
from Agents.CheckerAgent import CheckerAgent
from Agents.WriterSuperVisor import WriterSuperVisor
from Agents.SelectorAgent import SelectorAgent
from Services.ResearchService import ResearchService
from Services.TtsService import TtsService
from Agents.ImagePlannerAgent import ImagePlannerAgent
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
image_planner_config = config_loader.get_agent("imagePlannerAgent")
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



image_planner_model_name = image_planner_config["model"]
image_planner_model_config = models[image_planner_model_name]
image_planner_provider_name = image_planner_model_config["provider"]
image_planner_provider_config = providers[image_planner_provider_name]

image_planner_llm_client = AiClientFactory.create(
    image_planner_provider_config,
    image_planner_model_config
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

image_planner_agent = ImagePlannerAgent(
    image_planner_config,
    image_planner_llm_client
)

workflow = Workflow(
    research_service,
    selector_agent,
    supervisor_agent,
    ttsService=tts_service,
    imagePlannerAgent=image_planner_agent
)

print(workflow.run(rss_config))
