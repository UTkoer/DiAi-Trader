# 增强版独立 LLM 预测

本目录替代此前未完成的占位实现。每个 LLM 使用同一日期、同一指数的相同历史行情、统计特征和新闻快照独立预测；不协作、不投票、不训练量化模型、不切换到其他模型、不执行交易。

## 快速开始

在项目根目录执行（Python 3.10+；运行器仅依赖标准库）：

```powershell
cd E:\python-project\DiAi-Trader
python -m predict_advanced.runner --dry-run
python -m predict_advanced.runner --validate
python -m predict_advanced.runner
```

- `--dry-run`：打印输入预览，无 API 调用、不创建或修改结果文件，不要求密钥。
- `--validate`：校验模型配置、环境变量和行情，不调用 API、不写文件；查看预览中的 data_quality。
- 无模式参数：真实付费调用。默认仅启用 `glm-5.3<advanced>`，日期为 20260810，预测本地全部指数。
- `python -m predict_advanced.run_test` 为同一入口。请在项目根目录使用 `-m`，不要直接执行包内文件。
- 单日覆盖配置：`python -m predict_advanced.runner --date 20260807`。
- 其他配置：`python -m predict_advanced.runner --config predict_advanced/config.json`。

API Key 从 `api_key_env` 指定的进程环境变量读取；入口也读取根目录 `.env`，不会覆盖已有环境变量。默认变量名为 `AUTODL_API_KEY`。不要在 config.json 中填写密钥。未验证服务商当前模型是否可用，请核实你的账户权限；本次实现不发出真实模型请求。

## 模型名称与旧展示系统兼容

config.json 中：

```json
{
  "name": "glm-5.3<advanced>",
  "basemodel": "glm-5.3",
  "enabled": true,
  "openai_base_url": "https://www.autodl.art/api/v1",
  "api_key_env": "AUTODL_API_KEY",
  "temperature": 0.1,
  "json_mode": false
}
```

- 展示名称 `agent_name` 保留字面后缀 `<advanced>`。
- API 请求 `model` 使用 `basemodel`，不添加后缀。
- Windows 目录不能包含尖括号，因此目录为 `data/predict/glm-5.3_advanced/20260810.json`。
- 未带后缀的 name 也自动追加 `<advanced>`；净化后目录名重复会报配置错误。
- 展示后台扫描 data/predict 下子目录，因此无需修改展示系统。旧预测字段全部保留，新信息是额外字段。
- 所有预计输出在请求 API 前检查；任何同名文件已存在，整批拒绝运行。没有强制覆盖选项。重复实验请使用不同 name（如 `glm-5.3-run2<advanced>`），basemodel 保持不变。
- 结果先写同目录临时文件，再以不覆盖的硬链接发布；若文件系统不支持硬链接，会报错而不是退化为不安全覆盖。
- 本轮不修复上轮误覆盖的四个旧结果，没有声称恢复其原始内容。

## 日期、数据与新闻

- 数据源沿用 `data/Astocks/indices/*.json`，格式是 `{ts_code, name, records}`；原数据获取脚本仍为 `data/get_Astocks_indices.py`，无需复制下载逻辑。
- `prediction_date` 非空优先使用单日；设为 `""` 时按 start/end 范围内实际交易日期升序执行全部日期。
- 默认依据本地行情日期验证交易日；预测未来日期需提供 `trading_calendar` 文件，内容为交易所开市日期 JSON 数组，不以普通工作日替代节假日日历。
- 默认 cutoff 为目标日北京时间 00:00，统一保存成 UTC。行情必须早于目标日且其 15:00 收盘时间不晚于 cutoff。
- 单日可指定带时区的 `prediction_cutoff_datetime`，只能提前，不能晚于目标日开始；范围预测禁止共用一个显式 cutoff。
- 默认窗口 30 个交易日，足以计算 20 日收益和波动；不足窗口、重复日期、非法行情或指定日历缺失交易日会阻止该指数调用模型。未提供交易日历会标记缺失交易日无法核验，不假定数据完全合格。
- 新闻默认禁用。`news_cache` 可指定 JSON 数组或 JSONL；不读取旧的实时新闻搜索结果，不使用 `time_range` 替代逐条校验。
- 严格要求 `published_at <= fetched_at <= cutoff`；不接受无时区、缺失时间或 cutoff 后抓取的历史文章，防止把事后修订内容当作当时已知信息。
- 新闻必须带目标指数的 `index_codes` 标记；按 URL、相似标题及可选 event_id 去重。事件类型不明时保留 unknown，不伪造模型事件分析。
- 每条接纳/拒绝新闻保留 source、published_at、fetched_at、timestamp_valid 和原因审计。无新闻或新闻文件损坏不阻断行情预测。

新闻记录示例（请用真实、当时保存的记录，不要把该示例当作证据）：

```json
{
  "title": "事件标题",
  "url": "https://example.com/article",
  "source": "来源名称",
  "published_at": "2026-08-07T10:00:00+08:00",
  "fetched_at": "2026-08-07T10:05:00+08:00",
  "index_codes": ["000001.SH"],
  "content": "当时保存的正文片段"
}
```

## 输入、输出与可观测性

Prompt 包含指数名称/代码、预测目标、时点、行情、结构化收益/均线/波动/量价/影线/连涨跌等统计特征、有效新闻、数据质量、输出协议及不确定性要求。同一份 Prompt 在各模型间复用；不包含目标日行情、actual 或其他模型输出。

输出强制校验有限数字、概率和、方向/四分类/预期收益一致性、区间包含预期收益、正反证据、风险及反方观点；无效输出有限重试，不静默修复概率。旧 confidence 字段为 max(probability_up, probability_down) × 100，不代表校准后的成功率。风控仅补充 effective_confidence_level 与 no_trade，不改写原始模型观点。

旧兼容字段结构：

```json
{
  "agent_name": "glm-5.3<advanced>",
  "prediction_date": "20260810",
  "generated_at": "2026-09-13T00:00:00+00:00",
  "lookback_days": 30,
  "data_policy": "Completed sessions before target date and cutoff",
  "predictions": [{
    "ts_code": "000001.SH",
    "name": "上证指数",
    "as_of_date": "20260807",
    "lookback": [],
    "news_context": null,
    "prediction": {
      "direction": "up", "candle_class": "up", "confidence": 55,
      "expected_pct_change": 0.2, "rationale": "示例依据", "market_analysis": "示例分析"
    },
    "actual": null,
    "verification": null
  }]
}
```

这是旧展示字段的缩略示例；实际文件额外保存概率、区间、evidence、risks、counter_view、schema_version、run_id、model_name、参数、新闻审计、数据质量、风险控制、Prompt 哈希及请求 attempts。实际结果只在模型完成后附加，且不进入 Prompt。

每个请求记录开始/结束时间、耗时、HTTP 状态、request_id、token 数量（若返回）、解析状态和错误分类。日志不保存密钥、Authorization、完整请求、响应或服务商错误正文。429/500/502/503/504、网络失败与解析失败有限重试；400 等不重试。没有跨模型故障转移。使用标准库 socket 超时，不是独立连接/读取超时或整轮硬截止时间。每次保存打印 JSON 日志，可用 PowerShell 重定向到自行选择的日志文件。

## 评估与测试

```powershell
python -m predict_advanced.evaluate_results data/predict
python -m unittest discover -s predict_advanced/tests -v
python -m compileall -q predict_advanced
```

测试全部使用临时目录与 mock HTTP，不访问真实模型、不修改旧结果。没有额外运行依赖；测试用标准库 unittest，也可在已安装 pytest 的环境运行 `python -m pytest predict_advanced/tests`。

评估只读取 `_advanced` 目录下 schema_version 为 advanced-1.0 的保存结果，不请求模型，不混入旧实验。包括准确率、二/四分类混淆矩阵、precision/recall、Brier、Log Loss、概率校准分箱、置信度分组、模型/指数/日期/趋势分组，以及事后模型分歧统计。没有 actual 的结果不计准确率；API、解析、数据失败分开计数。方向乘实际收益只是描述性统计，不是可交易回测收益。

未来预测 actual 为 null 时，旧展示后台可从后续本地行情动态验证而不改写文件；本评估器只认保存的 actual，因此这类样本仍待验证。当前未实现自动补标签、重放调用或独立实时新闻下载命令。

## 已知限制

时点过滤不能消除 LLM 训练语料记忆历史走势的污染，也无法证明本地行情未被事后修订或新闻时间字段真实。原始行情没有可信版本时间，新闻需可信归档；本系统不能保证零未来信息风险。来源质量、交易日历和概率校准仍需人工审查，模型版本和服务商变化也会影响复现。不同模型 temperature 可配置但会影响公平比较；Kimi 示例沿用旧配置 temperature=1，不保证所有服务商支持同一参数。

这是一套独立 LLM 研究评估流程，不是投资建议，也不承诺预测准确率或收益。
