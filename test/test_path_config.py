import json
from pathlib import Path

print(Path(__file__).resolve().parents[1])

config_path = Path(__file__).resolve().parents[1] / "configs" / "astock_config.json"

with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

agent_type = config.get("agent_type", "BaseAgent")

print(agent_type)

#config_path = Path(__file__).parent / "configs" / "astock_config.json"