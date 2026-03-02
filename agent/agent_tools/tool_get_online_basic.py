import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastmcp import FastMCP
import pandas as pd
import tushare as ts

load_dotenv()

# 初始化 FastMCP
mcp = FastMCP("TushareBakBasic")

# 项目根目录（根据你的项目结构调整）
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.general_tools import get_config_value  # 假设你有这个工具函数

# 请确保已设置 TUSHARE_TOKEN 环境变量
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN") or get_config_value("TUSHARE_TOKEN", None)


def _validate_trade_date(date_str: str) -> None:
    """验证交易日期格式 YYYYMMDD 或 YYYY-MM-DD"""
    if len(date_str) == 8:
        try:
            datetime.strptime(date_str, "%Y%m%d")
            return
        except ValueError:
            pass
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return
    except ValueError:
        raise ValueError("trade_date 必须是 YYYYMMDD 或 YYYY-MM-DD 格式")


@mcp.tool()
def get_bak_basic(
    trade_date: str,
    ts_code: Optional[str] = None,
    limit: int = 7000,
    fields: str = None
) -> Dict[str, Any]:
    """
    获取指定交易日期的股票备用基础列表（bak_basic），包含市值、财务估值等指标。
    数据从2016年开始，单次最大返回约7000条记录。一般来说包含：
    pe/pb/eps/total_share/float_share/profit_yoy 这几个核心指标就能满足大部分筛选和分析需求。

    推荐用法：
    - 查询某一天全市场股票基本面快照：只传 trade_date
    - 查询某只股票某一天的数据：同时传 trade_date 和 ts_code

    Args:
        trade_date: 交易日期，支持两种格式：
                    - '20211012' (推荐，Tushare官方格式)
                    - '2021-10-12'
        ts_code:    可选，TS股票代码（如 '600519.SH'），为空则返回全市场
        limit:      最大返回条数，默认7000（接口单次上限）
        fields:     可选，包括： fields = (
                        "trade_date,ts_code,name,industry,area,"
                        "pe,pb,eps,bvps,total_share,float_share,"
                        "total_assets,liquid_assets,fixed_assets,"
                        "reserved,reserved_pershare,"
                        "list_date,undp,per_undp,"
                        "rev_yoy,profit_yoy,gpr,npr,holder_num")
                    返回字段列表用逗号分隔，例如：
                    'trade_date,ts_code,name,industry,pe,pb,eps,total_share'

    Returns:
        Dict 包含以下键：
        - success: bool
        - data: list[dict] 或 [] （每条记录为股票信息字典）
        - count: 实际返回记录数
        - trade_date: 查询的交易日期（标准化为 YYYYMMDD）
        - error: 错误信息（失败时存在）

    示例调用：
        get_bak_basic(trade_date="20250110")
        get_bak_basic(trade_date="20250110", ts_code="600519.SH")
        get_bak_basic(trade_date="20211012", fields="trade_date,ts_code,name,industry,pe,pb")
    """

    if not TUSHARE_TOKEN:
        return {"success": False, "error": "TUSHARE_TOKEN 未配置，请设置环境变量或配置文件"}

    # 标准化日期为 YYYYMMDD
    trade_date_clean = trade_date.replace("-", "")
    try:
        _validate_trade_date(trade_date_clean)
    except ValueError as e:
        return {"success": False, "error": str(e), "trade_date": trade_date}

    try:
        pro = ts.pro_api(TUSHARE_TOKEN)

        # 准备 fields 参数
        if fields:
            fields = fields.strip()
        else:
            fields = (
                "trade_date,ts_code,name,industry,area,"
                "pe,pb,eps,bvps,total_share,float_share,"
                "total_assets,liquid_assets,fixed_assets,"
                "reserved,reserved_pershare,"
                "list_date,undp,per_undp,"
                "rev_yoy,profit_yoy,gpr,npr,holder_num"
                ) # 默认常用字段（可根据需要调整）

        df = pro.bak_basic(
            trade_date=trade_date_clean,
            ts_code=ts_code,
            limit=limit,
            fields=fields
        )

        if df is None or df.empty:
            return {
                "success": True,
                "data": [],
                "count": 0,
                "trade_date": trade_date_clean,
                "message": "该日期没有数据或接口返回为空"
            }

        # 转换为列表 of dict，便于大模型处理
        records = df.to_dict(orient="records")

        return {
            "success": True,
            "data": records,
            "count": len(records),
            "trade_date": trade_date_clean,
            "fields": list(df.columns),
            "sample": records[:3] if records else None  # 前3条作为预览
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Tushare API 调用失败: {str(e)}",
            "trade_date": trade_date_clean,
            "hint": "请检查：1.Token是否有效 2.积分是否足够 3.日期是否为交易日"
        }


if __name__ == "__main__":
    port = int(os.getenv("BAKBASIC_HTTP_PORT", "8003"))
    print(f"启动 bak_basic 服务，端口: {port}")
    mcp.run(transport="streamable-http", port=port)