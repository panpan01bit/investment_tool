# 搜索与量化：设计说明与扩展指南

## 搜索层（investlab/search）

**目标**：给简报、个股研究、追问提供「有来源的事实补充」，并与行情等确定性数据严格区分（`[web]` 徽章）。

| Provider | 密钥 | 说明 |
| --- | --- | --- |
| DuckDuckGo (`ddgs`) | 不需要 | 默认启用，免费 |
| Tavily REST | `TAVILY_API_KEY` | 配置后叠加，结果更稳 |

聚合策略：
1. 各 provider 结果合并 → URL 规范化去重（域名字段+路径词排序哈希）；
2. 域名多样性惩罚（同域名重复出现次序降权）+ 权威财经域名加权（reuters/caixin/cls/eastmoney/…）；
3. TTL 缓存 30 分钟；请求预算按 `INVESTLAB_SEARCH_MAX_RESULTS` 控制。

**换源 / 加源**：在 `search/__init__.py` 增加一个 `_xxx_search()` 返回统一 `SearchHit{title,url,snippet,provider,badge}`，挂进 `search()` 的合并序列即可。SearXNG 自托管、Bing API、Serper 等都是十几行的事。注意出站必须走 `netguard.http_post_json/http_get`。

## 量化层（investlab/quant）

### 指标库
纯 numpy/pandas 实现（零 C 依赖）：`sma/ema/macd/rsi/kdj/bollinger/obv/williams_r/atr/annualized_volatility/max_drawdown/sharpe_ratio/beta/snapshot_indicators`。
需要更深指标时可加装 `pandas-ta`（130+ 指标）或 TA-Lib——在 `indicators.py` 里补对应函数即可，接口保持 Series in/out。

### 信号引擎
规则 = `(name, direction ±1, weight 1–3, reason)`，覆盖：
- 趋势：均线排列、韦恩斯坦四阶段（价格 vs MA200 斜率）
- 动能：MACD 金叉/红柱、RSI 超买超卖、KDJ J 值
- 位置/量能：52 周高点距离、放量倍数、OBV 斜率

综合分 `-100..100 = Σ(w·dir)/Σw 归一化`，任何一条规则的数据缺失都会进 `gaps` 展示而不是沉默。
想加 UZI 式更多流派规则（格雷厄姆估值线、北向资金等），在 `signals.compute_signals` 里加 `add(...)` 行即可。

### 回测
教学级向量化引擎：`sma_cross / rsi_reversion / breakout_20` 三策略 + 手续费滑点 + 净值曲线与基准对照。
严肃因子研究建议（参考 awesome-systematic-trading 清单）：

```bash
uv pip install vectorbt        # 大规模参数扫描
# 或 qlib（微软）：AI 因子流水线
```
把 `datasources.candles.get_candles()` 当标准数据喂入这些框架即可复用全部数据链。

### 赛道筛选（track screener）
`tracks/taxonomy.json` 固化了研究报告的赛道框架（18 二级 + 8 三级 + Top10 推荐排序 + 验证指标清单）。
- `screen_track('optical-module')`：代表标的批量信号矩阵；
- `screen_portfolio_lenses()`：A类(capex)/B类(生产率)两把尺子 + Top10。
更新观点时直接改 taxonomy.json（记得同步测试里对二级=18/三级=8 的断言，或改用下限断言）。

### 数据缺口纪律
所有面向用户的缺口都会带原因字符串出现在：晨报笔记、深度分析笔记 `⚠️ 数据缺口` 区块、API `gaps` 字段。**永远不要为缺数据造默认值。**
