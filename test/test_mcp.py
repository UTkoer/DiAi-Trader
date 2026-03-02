import os
import sys
import subprocess
from pathlib import Path

#------------------- test mcp -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
file_path = PROJECT_ROOT / "agent" / "agent_tools" /"tool_trade.py"

# 启动端口
with open('./trade_tool.log', "w") as f:
    process = subprocess.Popen([sys.executable, file_path], stdout=f, stderr=subprocess.STDOUT, cwd=os.getcwd())



# ports = {
#             "math": int(os.getenv("MATH_HTTP_PORT", "8000")),
#             "search": int(os.getenv("SEARCH_HTTP_PORT", "8001")),
#             "trade": int(os.getenv("TRADE_HTTP_PORT", "8002")),
#             "price": int(os.getenv("GETPRICE_HTTP_PORT", "8003")),
#             "crypto": int(os.getenv("CRYPTO_HTTP_PORT", "8005")),
#         }

# mcp_server_dir = "./agent/agent_tools/"
# service_configs = {
#             "math": {"script": os.path.join(mcp_server_dir, "tool_math.py"), "name": "Math", "port": ports["math"]},
#             "search": {"script": os.path.join(mcp_server_dir, "tool_alphavantage_news.py"), "name": "Search", "port": ports["search"]},  
#             "trade": {"script": os.path.join(mcp_server_dir, "tool_trade.py"), "name": "TradeTools", "port": ports["trade"]},
#             "price": {"script": os.path.join(mcp_server_dir, "tool_get_price_local.py"), "name": "LocalPrices", "port": ports["price"]},
#             "crypto": {"script": os.path.join(mcp_server_dir, "tool_crypto_trade.py"), "name": "CryptoTradeTools", "port": ports["crypto"]},
#             # "search": {"script": "tool_jina_search.py", "name": "Search", "port": self.ports["search"]},
#         }
# script_path = service_configs["trade"]["script"]
# #process = subprocess.Popen([sys.executable, script_path], stderr=subprocess.STDOUT, cwd=os.getcwd())

# def _get_default_mcp_config() -> Dict[str, Dict[str, Any]]:
#     """Get default MCP configuration"""
#     return {
#         "math": {
#             "transport": "streamable_http",
#             "url": f"http://localhost:{os.getenv('MATH_HTTP_PORT', '8000')}/mcp",
#         },
#         "stock_local": {
#             "transport": "streamable_http",
#             "url": f"http://localhost:{os.getenv('GETPRICE_HTTP_PORT', '8003')}/mcp",
#         },
#         "search": {
#             "transport": "streamable_http",
#             "url": f"http://localhost:{os.getenv('SEARCH_HTTP_PORT', '8004')}/mcp",
#         },
#         "trade": {
#             "transport": "streamable_http",
#             "url": f"http://localhost:{os.getenv('TRADE_HTTP_PORT', '8002')}/mcp",
#         },
#     }

# mcp_config = _get_default_mcp_config()
# print(mcp_config)
# client = MultiServerMCPClient(mcp_config)
# tools = await client.get_tools()

# print(tools)

# client = OpenAI()

# response = client.chat.completions.create(
#     model="MiniMax-M2",
#     messages=[
#         {"role": "system", "content": "You are a helpful assistant."},
#         {"role": "user", "content": "Hi, how are you?"},
#     ],
#     # Set reasoning_split=True to separate thinking content into reasoning_details field
#     extra_body={"reasoning_split": True},
# )

# print(f"Thinking:\n{response.choices[0].message.reasoning_details[0]['text']}\n")
# print(f"Text:\n{response.choices[0].message.content}\n")