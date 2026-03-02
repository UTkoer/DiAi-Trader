from openai import OpenAI
from typing import Any, Dict, List, Optional
from langchain_mcp_adapters.client import MultiServerMCPClient
import os
import subprocess
import sys

from dotenv import load_dotenv
load_dotenv()# Load environment variables

#------------------- test api -------------------------
import tushare as ts
from datetime import datetime
token = os.getenv("TUSHARE_TOKEN")
ts.set_token(token)
pro = ts.pro_api()
df = pro.daily(ts_code='600519.SH', start_date="20250101", end_date=datetime.now().strftime("%Y%m%d"))
print(df.columns)
