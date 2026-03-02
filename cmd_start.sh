#!/bin/bash  

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )" #获取项目根目录（scripts/ 的父目录）
PROJECT_ROOT="$( cd "$SCRIPT_DIR/" && pwd )"

cd "$PROJECT_ROOT" #A股数据准备
python ./data/A_stock/get_daily_price_tushare.py # # via tushare
python merge_jsonl_tushare.py 

echo "🔧 正在启动 MCP 服务..."
python ./agent/agent_tools/start_mcp_services.py
sleep 2

echo "🤖 Now starting the main trading agent..." # 运行A股配置
echo "🤖 正在启动主交易智能体（A股模式）..."
python main.py configs/astock_config.json  
echo "✅ AI-Trader 已停止"

echo "🔄 Starting web server..."
cd docs
python -m http.server 8888
echo "✅ Web server started"



# 🎉 5/5 MCP services running!
# 📋 Service information:
#   - Math: http://localhost:8000 (PID: 77320)
#   - Search: http://localhost:8001 (PID: 77321)
#   - TradeTools: http://localhost:8002 (PID: 77322)
#   - LocalPrices: http://localhost:8003 (PID: 77323)
#   - CryptoTradeTools: http://localhost:8005 (PID: 77324)