import json
import asyncio
from pathlib import Path
from agent.base_agent_astock import BaseAgentAStock
from agent.prompts.agent_prompt import all_sse_50_symbols # all_nasdaq_100_symbols

config_path = Path(__file__).parent / "configs" / "astock_config.json"
with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

enabled_models = [model for model in config["models"] if model.get("enabled", True)] # Get model list from configuration file (only select enabled models)
log_config = config.get("log_config", {})

for model_config in enabled_models: # Read basemodel and signature directly from configuration file
    model_name = model_config.get("name", "unknown")
    basemodel = model_config.get("basemodel")
    signature = model_config.get("signature")
    openai_base_url = model_config.get("openai_base_url",None)
    openai_api_key = model_config.get("openai_api_key",None)

stock_symbols = all_sse_50_symbols
log_path = log_config.get("log_path", "./data/agent_data") # Get log path configuration


agent_config = config.get("agent_config", {}) # Get agent configuration
base_delay = agent_config.get("base_delay", 0.5)
INIT_DATE = config["date_range"]["init_date"]
END_DATE = config["date_range"]["end_date"]
openai_base_url = "https://open.bigmodel.cn/api/paas/v4"
openai_api_key = "89f2ca54a8c945d29aa24de11af1836e.F6yLEkSRGbpI6lN1"

async def main():
    agent = BaseAgentAStock(
                    signature=signature,
                    basemodel=basemodel,
                    stock_symbols=stock_symbols,
                    log_path=log_path,
                    max_steps=3,
                    max_retries=3,
                    base_delay=base_delay,
                    initial_cash=1000,
                    init_date=INIT_DATE,
                    openai_base_url=openai_base_url,
                    openai_api_key=openai_api_key)

    await agent.initialize()
    await agent.run_date_range(INIT_DATE, END_DATE)

if __name__ == "__main__":
    asyncio.run(main())