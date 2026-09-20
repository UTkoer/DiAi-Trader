# data 目录数据脚本说明

本文档覆盖 `data` 目录顶层的全部 Python 数据获取/构建脚本。除特别说明外，命令均在项目根目录执行，Tushare 脚本默认从 `.env` 或环境变量读取 `TUSHARE_TOKEN`。

## 脚本总览

| 脚本 | 功能 | 主要数据源 | 默认输出 |
| --- | --- | --- | --- |
| `get_Astocks_data.py` | 下载全部上市 A 股日线、每日基本面，并计算技术因子 | Tushare | `data/Astocks/` |
| `get_Astocks_indices.py` | 增量下载常用 A 股指数日线 | Tushare | `data/Astocks/indices/` |
| `get_Astocks_index_factors.py` | 根据指数 OHLCV 计算每日技术因子；可选先刷新行情 | 本地 JSON，可选 Tushare | `data/Astocks/factors/` |
| `get_etf_data.py` | 增量下载全部上市场内 ETF、基金份额及技术因子 | Tushare | `data/ETFs/` |
| `get_data.py` | 按 `stocks.json` 中的自定义组下载 A 股/ETF，并转换为交易系统 JSONL | Tushare | `data/A_stock_data/` 与相对路径 `a_stock_data/` |
| `build_astock_symbol_groups.py` | 构建前端使用的 A 股指数、自选股代码分组 | Tushare + 本地文件 | `data/Astocks/symbols/` |
| `get_us_stocks_data.py` | 增量下载全部上市美股或指定美股，并计算技术因子 | Tushare | `data/USstocks/` |
| `get_us_stocks_data_akshare.py` | 使用 AkShare 增量下载全部或指定美股，并兼容 Tushare 版数据结构 | AkShare | `data/USstocks/` |
| `get_us_stocks_data_yfinance.py` | 使用本地代码列表增量下载美股，无需 Token 或远程列表接口 | yfinance | `data/USstocks/` |
| `build_market_layout.py` | 从单标的 JSON 自动生成完整市场目录、聚合文件和因子文件 | 本地 JSON | 指定市场目录 |

## 1. get_Astocks_data.py

完整的 A 股市场数据下载器。脚本先通过 `stock_basic` 获取沪、深、北交所全部上市股票，再按交易日调用 `daily` 和 `daily_basic`，合并 OHLCV 与 PE、PB、PS、市值、换手率、流通股本等字段，并计算动量、波动率、均线、均线偏离和量比等技术因子。

默认输出结构：

```text
data/Astocks/
├── stocks/<ts_code>.json   # 每只股票完整历史记录
├── daily/<YYYYMMDD>.json   # 按交易日切分，供前端读取
├── merged.json             # 全市场合并数据
├── meta.json               # 股票列表、名称、字段和日期范围
├── dates.json              # 可用交易日
└── latest.json             # 最新交易日快照
```

常用命令：

```bash
python data/get_Astocks_data.py --start 20250101 --end 20251231
python data/get_Astocks_data.py --incremental
python data/get_Astocks_data.py --build-partitions
```

可通过 `ASTOCKS_DATA_DIR` 修改默认输出目录。`--build-partitions` 不访问行情接口，只根据已有 `stocks/*.json` 重建前端分片。

## 2. get_Astocks_indices.py

下载上证指数、深证成指、创业板指、沪深 300、中证 500、中证 1000、中证 2000、上证 50、科创 50 共 9 个指数的日线行情。每个指数读取已有文件中的最后日期，只请求后续数据并按日期去重。

```bash
python data/get_Astocks_indices.py --start 20250101
```

输出为 `data/Astocks/indices/<指数代码>.json`，每个文件包含指数名称、起止日期和 `records` 历史记录。

## 3. get_Astocks_index_factors.py

读取 `data/Astocks/indices/*.json` 的指数 OHLCV，逐日计算均线、区间涨跌幅、MACD、KDJ、RSI、布林带、振幅、波动率、量比、RPS、均线状态、MACD 金叉/死叉、连续涨跌和 20 日新高/新低等指标。

```bash
python data/get_Astocks_index_factors.py --start 20250101 --only-new
python data/get_Astocks_index_factors.py --refresh --only-new
```

默认输出为 `data/Astocks/factors/<指数代码>/<YYYYMMDD>.json`。不加 `--refresh` 时完全使用本地指数数据；加上后会尝试通过 Tushare 补齐指数日线。

## 4. get_etf_data.py

通过 `fund_basic` 获取全部上市场内 ETF，使用 `fund_daily` 增量下载日线，使用 `fund_share` 补充基金份额，并据此计算市值和换手率。脚本还会添加与 A 股文件一致的动量、波动率、均线、均线偏离、量比等技术字段。

```bash
python data/get_etf_data.py --start 20250101
python data/get_etf_data.py --limit 10
```

默认输出结构为 `data/ETFs/stocks/`、`daily/`、`latest.json`、`dates.json`、`meta.json` 和 `symbols/etf.json`。`--limit` 只用于小规模调试。

## 5. get_data.py

较早的按组数据流水线。它读取 `data/stocks.json` 中的每个代码组，为每组下载上证 50 基准指数、A 股或 ETF 日线，生成合并 CSV、单标的 Alpha Vantage 风格 JSON、名称映射 CSV 和交易系统使用的 `merged.jsonl`。

```bash
python data/get_data.py
```

注意：该脚本同时使用 `data/A_stock_data/`、`data/a_stock_data/` 和相对当前工作目录的 `a_stock_data/`，且大小写不完全统一。运行前应确认当前目录和输出路径；如用于新流程，优先选择职责更明确的 `get_Astocks_data.py` 或 `get_etf_data.py`。

## 6. build_astock_symbol_groups.py

为市场页面生成小型股票代码分组文件。默认通过 `index_weight` 获取中证 2000、中证 1000、中证 500、沪深 300、上证 50、科创 50 的最新成分股；创业板分组由本地 `data/Astocks/stocks/` 中以 `300`、`301` 开头的深市代码生成；同时保留两个空的自选股分组。

```bash
python data/build_astock_symbol_groups.py
python data/build_astock_symbol_groups.py --local-only
```

`--local-only` 不访问 Tushare，只从本地文件生成可获得的分组。输出为 `data/Astocks/symbols/<分组名>.json`。

## 7. get_us_stocks_data.py

新增的美股日线下载器。默认通过 `us_basic` 获取全部当前上市美股，再使用 `us_daily` 按标的增量下载 OHLCV、成交额和 VWAP。也可通过命令行或 JSON 文件仅下载关注的股票。行情会统一补充 `pct_chg` 字段，并计算与 A 股/ETF 相同的技术因子。

```bash
# 下载全部上市美股
python data/get_us_stocks_data.py --start 20250101

# 只下载指定股票，适合首次验证或自选池
python data/get_us_stocks_data.py --symbols AAPL MSFT NVDA --start 20250101

# 从 JSON 数组或 {"symbols": [...]} 文件读取代码
python data/get_us_stocks_data.py --symbols-file data/us_symbols.json

# 小规模验证
python data/get_us_stocks_data.py --limit 10
```

默认输出结构：

```text
data/USstocks/
├── stocks/<symbol>.json
├── daily/<YYYYMMDD>.json
├── latest.json
├── dates.json
├── meta.json
└── symbols/us_stock.json
```

可通过 `--output` 或环境变量 `US_STOCKS_DATA_DIR` 修改保存目录。再次运行会从每只股票本地最后一个交易日的下一天开始下载；指定代码模式下，即使基础信息接口不可用，也会继续尝试下载输入代码。

## 8. get_us_stocks_data_akshare.py

不需要 Token 的 AkShare 美股下载版本。脚本通过 `stock_us_spot_em` 获取美股列表和 AkShare 内部代码，再通过 `stock_us_hist` 增量下载日线。AkShare 使用的 `105.AAPL` 等代码只用于请求，保存文件和元信息时统一使用 `AAPL`，因此可以与 Tushare 版共用 `data/USstocks/`。

```bash
# 下载 AkShare 返回的全部美股
python data/get_us_stocks_data_akshare.py --start 20250101

# 下载指定股票
python data/get_us_stocks_data_akshare.py --symbols AAPL MSFT NVDA

# 使用已有代码文件，并限制数量进行验证
python data/get_us_stocks_data_akshare.py --symbols-file data/us_symbols.json --limit 10

# 使用前复权行情；建议与默认不复权数据分目录保存
python data/get_us_stocks_data_akshare.py --symbols AAPL --adjust qfq --output data/USstocks_qfq
```

参数 `--adjust` 支持 `raw`、`qfq`、`hfq`，分别表示不复权、前复权和后复权。不同复权方式不应写入同一目录，使用 `qfq` 或 `hfq` 时请通过 `--output` 指定独立目录。输出文件结构与上一节完全一致，也可通过 `US_STOCKS_DATA_DIR` 修改默认目录。若美股列表接口临时不可用，可以直接传入 AkShare 完整代码（例如 `105.AAPL`）继续下载。

## 9. get_us_stocks_data_yfinance.py

无需 Token、也不调用远程股票列表接口的 yfinance 版本。股票代码由 `--symbols`、`--symbols-file` 或本地默认文件 `data/USstocks/symbols/us_stock.json` 提供，适合避开 AkShare/东方财富列表接口不稳定的问题。脚本自动读取每只股票本地最后日期并增量更新，保留已有股票名称和历史记录。

```bash
# 在项目根目录运行；未指定代码时读取默认 us_stock.json
python data/get_us_stocks_data_yfinance.py --start 20260101

# 在 data 目录运行，并显式指定现有代码文件
python get_us_stocks_data_yfinance.py --symbols-file USstocks/symbols/us_stock.json --start 20260101

# 直接下载指定股票
python data/get_us_stocks_data_yfinance.py --symbols AAPL MSFT NVDA

# 自动复权价格建议保存到独立目录
python data/get_us_stocks_data_yfinance.py --symbols AAPL --auto-adjust --output data/USstocks_adjusted
```

yfinance 的 `end` 参数本身不包含结束当天，脚本已自动加一天，因此 `--end` 指定的交易日会被包含。yfinance 不提供可靠的全市场股票列表，因此脚本不会尝试自动发现全部美股。普通代码直接使用；`BRK.B` 这类代码请求时会自动转换成 Yahoo 使用的 `BRK-B`，保存时仍保留原代码。默认价格不复权，并保留 `adj_close`；使用 `--auto-adjust` 时不要与不复权数据写入同一目录。

## 10. build_market_layout.py

统一数据后处理脚本。它读取指定市场目录下的 `stocks/*.json` 或 `indices/*.json`，自动生成 `daily`、`factors`、`indices`、`stocks`、`symbols` 五个目录，以及 `dates.json`、`latest.json`、`merged.json`、`meta.json` 四个聚合文件。`merged.json` 采用流式写入，避免一次性将完整市场数据加载到内存。

```bash
python data/build_market_layout.py --root data/USstocks --asset-type US_STOCK --source stocks
python data/build_market_layout.py --root data/Astocks --asset-type A_STOCK_INDEX --source indices
```

预测竞赛页面的“快速下载数据”会在行情下载成功后自动调用该脚本。A 股指数还会先调用 `get_Astocks_index_factors.py` 生成逐日指数因子，再执行完整结构编排。

## 数据更新建议

首次运行全市场脚本时数据量和 API 调用次数较大，建议先使用 `--limit` 或 `--symbols` 验证数据源。Tushare 版本需要检查 Token 权限，AkShare 版本无需 Token，但依赖公开数据接口的可用性。日常更新使用相同命令即可；A 股全市场脚本需显式加 `--incremental`，ETF、美股和指数脚本默认执行增量更新。
