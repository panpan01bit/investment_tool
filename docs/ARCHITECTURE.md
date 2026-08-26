# 架构（ARCHITECTURE）

## 设计原则

1. **本地优先**：数据（报告/笔记/持仓/缓存）全部留在本机；出站只访问公开行情与搜索 API。服务器部署接口保留但默认关闭。
2. **两阶段流水线**（借鉴 UZI-Skill）：
   - **阶段一 = 确定性计算**（Python）：取数、指标、信号、图表定位，产出结构化 JSON；
   - **阶段二 = LLM 叙述**：只能引用阶段一的数字；JSON schema 约束输出；缺数据显示 `--`/`null` 并记入 `gaps`，禁止编造。
3. **多路回退 + TTL 缓存**：行情/K线按“东财 → 腾讯 → akshare”逐级回退，SQLite→文件缓存分层 TTL（实时 60s / K线 2h / 基本面 12h / 搜索 30min）。任一源挂掉不阻塞整条链。
4. **安全基线**：
   - 出站请求统一过 `netguard.validate_url()`（仅 http/https，DNS 解析后拒绝私有/环回/保留地址，防 SSRF 与 DNS rebinding 的第一道闸）；
   - 密钥只从环境变量读取，`token_status()` 只回显“已配置/未配置”；
   - 文件接口用格式正则（日期、16位hex 报告ID）+ 目录包含校验双保险；
   - 上传内容以服务端 sha256 命名入库，路径不含用户输入。

## 模块图

```
                     ┌──────────────────────────────────────────┐
  CLI (typer) ──────▶│            investlab 核心                 │◀──── FastAPI (/api/*)
  investlab daily    │                                          │      前端 web/dist
  ...                │ ┌────────┐  ┌─────────┐  ┌────────────┐  │
                     │ │config.py│  │netguard │  │ utils(TTL) │  │
                     │ └────────┘  └─────────┘  └────────────┘  │
                     │  datasources(quotes/candles/fundamentals │
                     │     /macro/news)   search(DDG+Tavily)    │
                     │        ▲                    ▲            │
                     │  quant(indicators→signals→backtest,     │
                     │        portfolio,screener)              │
                     │        ▲                                │
                     │  reports(meta→text→figures→vision)      │
                     │        ▲                                │
                     │  analysis(briefing/deep_analysis/chat)  │
                     │        ▼                                │
                     │  obsidian/Vault ──▶ 你的 Vault           │
                     │  tracks/taxonomy.json（投资主线框架）      │
                     └──────────────────────────────────────────┘
```

## 数据流举例：今日晨报

1. `analysis/briefing.run_daily()` 读 Obsidian `50 组合/holdings.csv`（或自动迁移 legacy xlsx）；
2. `datasources.quotes` 批量行情（60s 缓存）；`macro` 宏观一页纸（本地优先 → akshare 在线）；
3. `datasources.news.fetch_all()` 抓 RSS → watchlist 匹配 → 24h 焦点 / 30d 背景；
4. 每个持仓 `tracks_for_symbol()` 映射赛道（A/B 类）；
5. 以上落盘 `data/briefings/<date>.json`（阶段一完成，随时可查）;
6. LLM（think 模型）生成叙述 + 失败兜底模板 → 渲染 frontmatter 笔记写入 `10 听涛日报/YYYY-MM-DD 听涛晨报.md`。

## 数据流举例：券商报告

1. `ingest_pdf_bytes()`：内容哈希 → `data/reports/library/<rid16>/report.pdf`（幂等）；
2. `text_extract.extract_pdf()`：逐页文本 + 标题层级；
3. `figures.extract_figures()`：位图（get_images 过滤 logo）+ 矢量密集区聚类（get_drawings 合并框，过滤分隔线/背景框）→ PNG；
4. 可选 `vision.analyze_figure()`：视觉模型分类（统计图/竞品对比/示意/照片/logo）+ series 数值结构化，置信度低强制人工核对标记；
5. `llm_summarize()`：核心观点/逻辑/风险 JSON；
6. `export_to_obsidian()`：`30 报告库/<日期 券商 标题>/` 下 note.md + figures/ 附件 + 更新 Index 表格。

## 部署预留（暂时不上线）

当前默认：`INVESTLAB_API_HOST=127.0.0.1`、`INVESTLAB_AUTH_DISABLED=1`。将来上线时：

1. `.env` 设 `INVESTLAB_API_HOST=0.0.0.0`、`INVESTLAB_AUTH_DISABLED=0`、强随机的 `INVESTLAB_ADMIN_PASSWORD`；
2. （可选）反代 nginx/Caddy 到 `investlab serve` 的端口，`INVESTLAB_CORS_ORIGINS` 填正式域；
3. 鉴权实现为 Bearer token = `sha256(admin_username:admin_password)`（`api/main.require_auth`，常量时间比较）；多人协作建议再演进为用户表 + 轮换令牌；
4. 注意 legal/合规：搜索结果、研报解析摘要仅供个人研究，不要对外分发原文内容。

## 已知取舍

- 回测是教学级向量化引擎（单标的、全仓进出），因子级研究建议接 vectorbt/qlib（见 requirements 注释与 docs/SEARCH_QUANT.md）；
- 视觉图表数值化精度取决于模型与图片清晰度，所有输出带 `needs_manual_check` 标志，重要数字请对照原文核对；
- 新浪/雪球等未纳入直接依赖的源只有东财/腾讯/akshare 三路，极端情况下全部不可达会返回空并记入 gaps。
