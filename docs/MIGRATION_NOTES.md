# v1 → v2 迁移说明

本文记录 2026-08 重构（`local-first-refactor` 分支）中每个旧组件的去向。
**旧组件已从工作树移除**（保持仓库为干净的生产级项目），完整历史内容可在重构前提交
`f5393b0` 中找到，例如：`git show f5393b0:macro-bot/bot.py`。

## 一览

| 旧位置（git 历史） | 去向 | 说明 |
| --- | --- | --- |
| `macro-bot/bot.py`（cron 主流程） | 重写为 `src/investlab/analysis/briefing.py` + CLI `investlab daily` | 飞书推送改为 Obsidian 笔记；Kimi 调用统一进 llm/client |
| `macro-bot/app.py`（Flask API） | 重写为 `src/investlab/api/main.py`（FastAPI，28 路由） | 删除：`/api/interviews`、`/api/parse`、`mock_feishu` 相关；`/api/chat` 保留并简化为无会话表实现 |
| `macro-bot/lark_interviews.py` | **删除**（飞书访谈库功能随需求下线） | 若将来需要，可从 git 历史恢复后改造成“本地 SQLite 专家库” |
| `macro-bot/mock_feishu.py` | **删除**（飞书整体移除） | — |
| `macro-bot/static_server.py` + `guanlan/` 编译产物 | 替换为全新 `web/`（Vite React TS 源码工程） | 旧 SPA 的安全鉴权绕过补丁不再存在；新前端默认 auth off 本机模式 |
| `macro-bot/model_router.py` | 并入 `src/investlab/llm/client.py`（fast/think 双角色） | 用量日志移到 `data/logs/llm_usage.jsonl` 且不打印 key 前缀 |
| `macro-bot/holdings.py` | `quant/portfolio.py` | 兼容迁移入口保留：把旧 `holdings.xlsx` 放到 `.env` 的数据目录或 Vault 根下会自动转 CSV |
| `macro-bot/news_fetcher.py` / `daily-news-fetcher/*` | 精简合并为 `datasources/news.py` + `data/watchlist.json` | RSS 抓取(feedparser)与关键词匹配保留；LLM 打标可作为后续增强（prompts 在历史代码里） |
| `macro-bot/akshare_macro.py` | `datasources/macro.py` | 在线拉取为主；兼容读取旧 `data/news/akshare/YYYY-MM-DD.json` 缓存 |
| `macro-bot/kimi_datasource_*` + scripts/* systemd 刷新 | **归档至 git 历史**（OAuth 插件方案不再默认启用） | 本地模式下 Tushare/FRED/akshare 已覆盖大部分场景；需要 Wind/天眼查等会员源时按历史里 MCP 客户端思路加回独立模块 |
| `finance-mcp/`（TS MCP server） | 移除（git 历史保留） | 其 Tushare 工具集由 Python 原生 datasources 取代；确需 stdio MCP 服务可从历史恢复独立运行 |
| `stock-tweet-bot/` | 移除（git 历史保留） | X 平台摘要依赖 xAI key；如需启用可恢复并对接新简报的 news 部分 |
| `stock-tweet-grok-trader/` | 移除（git 历史保留） | Polymarket 独立交易助手，与新主线无关但历史上可用 |
| `tests/test_*.py`（针对旧代码） | 移除（git 历史保留） | 新套件在根目录 `tests/`，52 例 |

> 安全提示：历史的 macro-bot/daily-news-fetcher 等脚本存在若干未修复的 SSRF/XXE/路径拼接问题（本轮审计标记），这是将其移出工作树的原因之一——请勿直接把历史版本跑在暴露网络上。

## .env 变量对照

| 旧变量 | 新变量 | 备注 |
| --- | --- | --- |
| `KIMI_API_KEY` | `INVESTLAB_LLM_API_KEY` | 旧名仍被读取（兼容） |
| `KIMI_BASE_URL` | `INVESTLAB_LLM_BASE_URL` | 同上 |
| `KIMI_MODEL` / `KIMI_STRONG_MODEL` | `INVESTLAB_LLM_MODEL` / `INVESTLAB_LLM_THINKING_MODEL` | 同上 |
| `TAVILY_API_KEY` | 不变 | — |
| `TUSHARE_TOKEN` | 不变 | — |
| `FEISHU_WEBHOOK` / `FEISHU_SIGN_SECRET` | **废弃** | 不再读取 |
| `LARK_APP_ID` 等 | **废弃** | 飞书访谈库下线 |
| `ADMIN_USERNAME/PASSWORD`, `INTERNAL_SECRET`, `APP_CORS_ORIGINS` | 对应 `INVESTLAB_ADMIN_*` / `INVESTLAB_INTERNAL_SECRET` / `INVESTLAB_CORS_ORIGINS` | 旧名也兼容读取 |
| （新增） | `INVESTLAB_OBSIDIAN_VAULT` | **必须配置**：你的 Vault 路径 |
| （新增） | `INVESTLAB_LLM_VISION_MODEL` | 券商报告图表理解 |

## 新增需要你做的事

1. 复制 `.env.example` → 根目录 `.env`，填 LLM key 与 Vault 路径；
2. `pip install -e ".[cn,dev]" && investlab init-vault && investlab doctor`；
3. 把现有持仓整理成 `50 组合/holdings.csv`（或把旧 holdings.xlsx 放到数据目录自动迁移）；
4. crontab 如需继续每天自动晨报：把旧的 `bot.py` 行替换成
   `0 8 * * * cd <repo> && .venv/bin/investlab daily >> data/logs/cron.log 2>&1`
