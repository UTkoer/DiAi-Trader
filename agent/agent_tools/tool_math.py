import os
from fastmcp import FastMCP
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from agent.general_tools import get_config_value

from dotenv import load_dotenv
load_dotenv()

mcp = FastMCP("Math")

@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers (supports int and float)"""
    # log_file = get_config_value("LOG_FILE")
    # signature = get_config_value("SIGNATURE")
    # log_entry = {
    #     "signature": signature,
    #     "new_messages": [{"role": "tool:add", "content": f"{a} + {b} = {float(a) + float(b)}"}]
    # }
    # with open(log_file, "a", encoding="utf-8") as f:
    #     f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    return float(a) + float(b)


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """Multiply two numbers (supports int and float)"""
    # log_file = get_config_value("LOG_FILE")
    # signature = get_config_value("SIGNATURE")
    # log_entry = {
    #     "signature": signature,
    #     "new_messages": [{"role": "tool:multiply", "content": f"{a} * {b} = {float(a) * float(b)}"}]
    # }
    # with open(log_file, "a", encoding="utf-8") as f:
    #     f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    return float(a) * float(b)


if __name__ == "__main__":
    port = int(os.getenv("MATH_HTTP_PORT", "8000"))
    mcp.run(transport="streamable-http", port=port)
