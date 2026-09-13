# 股票特征分析 MCP 工具

这是独立的只读 MCP 服务，使用与 `predict/mcp_tools/news_search_server.py` 一致的 `mcp.server.fastmcp.FastMCP` 方式注册工具，通过 stdio 通信。计算本身不需要 API Key、不联网、不读写数据文件、不训练模型。

## 安装和启动

Python 3.10+。请在**实际运行 MCP 服务的 Python 环境**安装依赖：

```powershell
cd E:\python-project\DiAi-Trader
python -m pip install -r predict_advanced/mcp_tools/requirements.txt
python -m predict_advanced.mcp_tools.feature_analysis_server
```

stdio 服务启动后等待 MCP 客户端输入，不会像普通命令那样打印分析报告，这是正常行为。通常由客户端启动，不需要手动保持一个终端。

当前测试环境中 PATH 的第一个 Python 是 MSYS Python，没有 pip。若你的环境相同，可选择已安装 pip 的解释器，例如：

```powershell
& 'D:\developtools\python3.12.8\python.exe' -m pip install -r predict_advanced/mcp_tools/requirements.txt
```

然后将 `config.json` 的 `command` 改为该解释器绝对路径。不要安装到一个环境，却用另一个环境启动。

本目录 `config.json` 提供常见 `mcpServers` 客户端配置；其中脚本是项目绝对路径，可从任意工作目录启动。将 `stock-features` 项合并到支持 MCP 的客户端配置，并按实际机器调整 Python 和项目路径。

**该配置不会被 `predict_advanced/runner.py` 自动加载。** 现有 runner 是单次聊天请求，不具备 tool-call 循环。本次新增的是可接入 MCP 客户端的服务；没有假装现有预测模型已经自主调用它，也没有修改旧结果格式。支持 MCP 的宿主负责向模型声明工具、执行调用、把工具结果回传给同一个模型，不涉及模型协作。

## 工具一：analyze_stock_features

输入一只股票或指数的一段**日线**数据，返回有时点边界的结构化特征。

参数：

| 参数 | 必填 | 含义 |
|---|---|---|
| `records` | 是 | 最多 5000 条 JSON 记录，单一证券；不能传文件路径 |
| `prediction_date` | 是 | 预测日期，YYYYMMDD 或 YYYY-MM-DD；当日及之后记录排除 |
| `prediction_cutoff_datetime` | 否 | 带时区 ISO 时间；默认目标日北京时间 00:00，只能提前 |
| `start_date` / `end_date` | 否 | 输入数据的筛选起止日期，闭区间；不额外读取暖启动数据 |
| `symbol` | 否 | 证券代码；与 records 中 ts_code 不一致时拒绝 |
| `trading_dates` | 否 | 官方日历开市日期数组，用于检查请求窗口中缺失或非法交易日 |

每条记录必须包含 `trade_date, open, high, low, close, vol`。`amount` 可选；`ts_code` 用于核对单一证券。`pct_chg/pre_close/change` 不参与计算，收益由所选区间内 close 重算。

为防止异常数值导致溢出，价格允许区间为 `[1e-12, 1e12]`，成交量为 0 或 `[1e-12, 1e25]`，成交额为 `[0, 1e25]`。不接受布尔值、数值字符串、NaN/Infinity。非法关键记录、重复日期、混合证券或官方交易日历缺失会返回 `status=error` 和空 features，不静默删掉问题行继续计算。

调用参数示例：

```json
{
  "symbol": "000001.SH",
  "prediction_date": "20260810",
  "start_date": "20260806",
  "end_date": "20260807",
  "records": [
    {"ts_code":"000001.SH","trade_date":"20260806","open":100,"high":103,"low":99,"close":102,"vol":1000,"amount":100000},
    {"ts_code":"000001.SH","trade_date":"20260807","open":102,"high":104,"low":101,"close":103,"vol":1200,"amount":123000}
  ]
}
```

上述行情是**合成演示数据，不是真实上证指数价格**。两条记录可计算 1 日收益，但不足 20 日指标，因此它们会为 null。希望得到 20 日收益和波动请提供至少 21 条；60 日指标至少 61 条。end_date 若写为目标日，目标日记录仍然被排除。

返回核心字段：

```json
{
  "schema_version": "stock-features-1.0",
  "status": "warning",
  "symbol": "000001.SH",
  "prediction_date": "20260810",
  "prediction_cutoff_time": "2026-08-09T16:00:00+00:00",
  "coverage": {"count": 2, "start_date": "20260806", "end_date": "20260807"},
  "features": {"return_1d_pct": 0.9803921568627416, "sma_20": null},
  "unavailable_features": {"sma_20": "requires_20_sessions"},
  "data_quality": {"errors": [], "warnings": ["some_features_unavailable_see_reasons"]}
}
```

这是缩略结构示例；实际结果还包含其他特征、单位、所选数据哈希、排除条数、观察描述和限制。`warning` 不代表整个工具失败，应检查具体缺失字段。未提供日历不能证明期间交易日连续，也不能完整发现头尾缺失。

## 特征清单

- **收益趋势**：1/3/5/10/20/60 日收益；5/10/20/60 日均线和偏离度；连涨/连跌天数。
- **波动风险**：5/10/20/60 日日收益波动率、窗口最大回撤；14 日简单 ATR 及其占现价百分比。
- **价格结构**：振幅、跳空幅度、实体和上下影线比例；当前价距**前 20 日（不含当前行）**最高/最低价的距离。
- **动量位置**：14 日简单 RSI；20 日布林带上下轨、带宽和 percent-b。
- **量价关系**：量变化率、当前量/前 20 日平均量、20 日平均成交额、20 日价格收益与成交量变化的相关性。
- **可解释观察**：如价格高于 SMA20、突破前 20 日高点，仅陈述特征事实，不直接转换成买卖信号。

定义有意保持透明：RSI 和 ATR 使用简单窗口平均，**不是 Wilder 平滑版本**；波动率是总体标准差、不年化；布林带为总体标准差的 2 倍；最大回撤是所选窗口内计算而不是全历史回撤。成交量/成交额沿用输入单位，**不把 amount / vol 当成 VWAP**，避免手/股及千元单位混淆。

短窗口、常量序列导致的相关系数未定义、零量分母等，均返回 null 和原因。零振幅 K 线三种比例定义为 0；完全横盘 RSI 定义为 50。

## 工具二：feature_definitions

无参数。返回所需字段、单位、窗口长度、计算公式与约束。模型不清楚指标口径时先调用它，再调用分析工具。

建议宿主给模型的工具使用说明：

> 先说明需要分析的历史范围，将该范围内单一证券的日线 records 传入 analyze_stock_features。调用后先检查 status、data_quality、coverage 和 unavailable_features。不要将 null 当作 0，不要将 RSI 或突破自动视为上涨概率。数据不足时只能请求截止时间之前的更多数据。

**时点边界应由可信宿主固定/复核，不能任由模型为了获取未来数据修改 prediction_date 或 cutoff。** 本工具保证相对所传边界的过滤，不能证明调用者传入的边界本身就是实验设定。它也无法识别历史行情事后复权/修订或 LLM 记忆污染。

## 不使用 MCP 的直接调用

分析函数只有标准库依赖，方便测试或以后接入运行器：

```python
from predict_advanced.feature_analysis import analyze_stock_features

result = analyze_stock_features(
    records=historical_rows,
    prediction_date="20260810",
    start_date="20260601",
    end_date="20260807",
    symbol="000001.SH",
)
```

## 验证

```powershell
python -m unittest discover -s predict_advanced/tests -v
python -m compileall -q predict_advanced
```

测试包括已知序列数值、横盘/零量、窗口不足、非法 OHLCV、日期和时区、未来记录不影响特征、日期排序、证券混合、交易日历和 MCP 工具注册。

`MCPStdioTests` 是真实子进程 initialize/list_tools/call_tool 测试：未安装 SDK 时明确跳过；SDK 安装成功后自动执行。此次环境缺 SDK 且依赖下载失败，不能声称真实 MCP 协议联调已通过。只读分析层和模拟注册测试不受此限制。
