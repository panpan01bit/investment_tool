# 观澜 · InvestLab v2

> 本地优先的投资研究工作台：**Obsidian 笔记 + 券商报告深度解析 + 互联网搜索 + 量化信号**，全程数据留在自己电脑上。
>
> 由原 `investment_tool`（macro-bot 飞书推送架构）重构而来，设计文档见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 这是什么

一套跑在 macOS / Linux 本机的投研流水线，服务「AI 生产端普及」主线研究（A股/港股/美股）：

```
券商报告 PDF ─┐
持仓 holdings ─┤   ┌→ 每日听涛晨报 ──→ Obsidian「10 听涛日报」
RSS 新闻      ─┼──▶│   个股深度研究 → Obsidian「20 个股研究」
宏观/行情/K线 ─┘   │   报告图表结构化 → Obsidian「30 报告库」
联网搜索(DDG/Tavily)┘   量化信号/回测/赛道筛选 → 本地 Web 控制台
```

核心能力：

| 能力 | 说明 |
| --- | --- |
| 📄 **券商报告解析** | PyMuPDF 文本+表格抽取；位图与矢量统计图区域自动定位导出 PNG；配置视觉模型后把柱状图/竞品对比图转成结构化数值表（不可读的字段留空、绝不编造） |
| 📝 **Obsidian 集成** | 所有产出写入你的 Vault（听涛晨报 / 个股研究 / 报告库），Dataview 友好的 frontmatter 与索引 |
| 🔍 **搜索聚合** | DuckDuckGo 免费内置，可选叠加 Tavily；去重、来源多样性加权、`[web]` 徽章与行情数据严格区分 |
| 📣 **社媒热度** | 跨源舆情脉冲（Reddit / Hacker News / Polymarket 赔率 / StockTwits 情绪 / GitHub 活动），按真实互动量打分 0-100，每源独立降级并如实报告状态（灵感致谢 [last30days-skill](https://github.com/mvanhorn/last30days-skill)）；`investlab pulse "liquid cooling"` |
| 📈 **量化** | 纯 pandas 实现的指标库（MA/MACD/RSI/KDJ/BOLL/OBV/ATR/波动率/回撤）、规则化信号引擎（-100~100 分+理由）、轻量向量化回测（均线交叉/RSI回归/突破）、组合视图、赛道筛选 |
| 🎛 **绩效与组合优化** | quantstats 绩效指标+HTML 报告（一键存入 Obsidian）；PyPortfolioOpt 建议权重（HRP/最大夏普/最小波动）与调仓清单 |
| 📟 **专业K线** | klinecharts 蜡烛图（A股配色：涨红跌绿）+ MA/VOL/MACD/RSI 四联副图，研究/信号页即点即看 |
| 🧭 **赛道框架** | 内置《AI 生产端普及·2026》18 个二级赛道 + 8 个三级赛道 taxonomy（含代表标的与验证指标清单），持仓自动映射到 capex链(A类)/生产率链(B类) |
| 💬 **研究问答** | 基于当日简报 + 联网搜索的追问（替代旧飞书机器人对话） |
| 📲 **手机推送** | 晨报生成后经 ntfy（可自托管）或 Bark(iOS) 推送通知——飞书推送的本地化替代 |
| 🖥 **Web 控制台** | React 全新前端：简报 / 持仓 / 研究 / 量化 / 报告库 / 问答 / 设置 |

## 快速开始（新用户 3 步）

需要 Python ≥3.10 与 Node ≥20。

```bash
# 1) 安装（建议装上 akshare 以启用 A股数据）
pip install -e ".[cn,dev]"

# 2) 一键初始化：自动生成你的 .env（API 专用文件）+ Obsidian Vault + 投研档案 + 观察清单
investlab init

# 3) 编辑 .env 填入唯一必填项 INVESTLAB_LLM_API_KEY，然后体检+开跑
investlab doctor
investlab daily        # 今日晨报 → Obsidian
investlab serve        # Web 控制台 http://127.0.0.1:8300
```

**API 密钥只放 `.env`**（模板见 `.env.example`，已被 gitignore，永不入库）。
唯一必填的是 `INVESTLAB_LLM_API_KEY`；Tushare/Tavily/FRED/GITHUB_TOKEN/手机推送均为可选增强，注释里有申请地址。

更多命令：

```bash
investlab analyze 300308                 # 个股深度分析（中际旭创）
investlab report ingest ~/Downloads/x.pdf
investlab report parse <报告id> --vision # 图表提取+视觉结构化
investlab signals 002837.SZ              # 英维克技术信号
investlab screener optical-module        # 光模块赛道观察矩阵
investlab portfolio-optimize --method hrp # 组合优化建议+调仓清单
investlab pulse "liquid cooling"          # 跨源舆情热度
investlab notify-test                    # 测试 ntfy/Bark 推送
investlab chat "液冷板块今天为什么涨？"    # 追问
investlab serve                          # Web 控制台 http://127.0.0.1:8300
```

前端开发模式（改 UI 时用）：`cd web && npm install && npm run dev`（Vite 会代理 `/api` 到 8300）。生产用法直接 `npm run build`，`investlab serve` 同域托管 `web/dist`。

## 供他人使用 / 换自己的投研主线

仓库内置的赛道框架与示例档案是**作者的投研主线**（AI 生产端普及，见 `src/investlab/profiles/ai-production.json`）。fork 之后换你自己的主线**不需要改代码**，三处个性化文件全部与你隔离、不入库：

| 想定制什么 | 放哪里 | 说明 |
| --- | --- | --- |
| **你的 API 密钥** | `.env`（`investlab init` 从 `.env.example` 自动生成） | 唯一放密钥的地方 |
| **研究主线/关注链/观察池/新闻源** | `data/profile.json`（`investlab init` 生成副本） | 可覆盖 `sector_focus` / `watchlist_seed` / `macro_keywords` / `company_keywords` / `news_sources` / `briefing` 等 |
| **赛道 taxonomy** | `src/investlab/tracks/taxonomy.json` | 换行业主线时整体替换（18+8 赛道/代表标的/验证指标） |
| **RSS 新闻源** | `data/sources.config.json`（优先于档案） | 完全自定义 |

档案切换：`.env` 里 `INVESTLAB_PROFILE=ai-production | template | /path/to/your.json`，或临时 `investlab profile <名字>` 查看/切换。合并优先级：`data/profile.json`（你的）＞ 内置档案 ＞ 代码默认。

> Fork 成你自己的投研工作室只需动三处：`.env`（密钥）+ `data/profile.json`（框架）+ `taxonomy.json`（赛道数据），欢迎把通用化改进 PR 回来。

## 密钥与安全

- 所有 token 只从环境变量 / `.env` 读取（支持新命名 `INVESTLAB_*` 与旧命名 `KIMI_API_KEY` 等），源码与示例中没有任何可用凭据；
- 出站请求统一经 `netguard` 校验：仅 http/https、拒绝内网/环回/保留地址（防 SSRF）；
- API 默认只绑定 `127.0.0.1`；路径参数严格白名单校验。**线上部署接口已预留但默认关闭**——见 `docs/ARCHITECTURE.md` 的“部署预留”一节；
- 本工具输出均为研究框架而非投资建议。

## 目录导航

```
src/investlab/
├── config.py        # 全部配置（env 别名兼容 macro-bot 时代变量）
├── netguard.py      # 出站请求安全网关
├── llm/             # OpenAI 兼容客户端（fast/think/vision 三角色模型）
├── obsidian/        # Vault 写入器（frontmatter/原子写/附件）
├── datasources/     # 行情(东财→腾讯→akshare)/K线/基本面/宏观/新闻 多路回退 + TTL缓存
├── search/          # DDG + Tavily 聚合搜索
├── quant/           # indicators / signals / backtest / portfolio / screener
├── reports/         # 券商PDF解析：文本/图形提取/视觉结构化/Obsidian导出
├── analysis/        # 每日简报 / 深度分析 / 对话追问（两阶段流水线）
├── tracks/          # 赛道 taxonomy（对齐研究报告第二部分）
├── api/             # FastAPI 本地服务（28 条路由，预留鉴权与部署开关）
└── cli.py           # typer 命令行
web/                 # React 前端源码（Vite + TS + ECharts）
tests/               # pytest 套件（指标/信号/回测/组合/PDF管线/API安全）
```

## 从 v1（服务器版）迁移了什么

| v1（飞书/服务器） | v2（本地） |
| --- | --- |
| Feishu webhook 富文本推送 | 写入 Obsidian Vault 笔记 |
| guanlan 编译产物前端（无源码） | 全新 Vite React TS 源码工程 |
| Kimi 单一调用点分散各文件 | 统一 LLM 客户端（模型分角色、用量记录） |
| 宝塔 systemd/crontab | `investlab` CLI + 本地 uvicorn（crontab 可继续配 `investlab daily`） |
| token 散落 .env 且部分打日志 | 单点 config；日志脱敏；无任何 key 打印 |

> ⚠️ v1 曾发生过 xAI key 泄露并已轮换；v2 在 lint 层禁掉了硬编码密钥模式，请继续保持所有密钥只在 `.env`。旧组件（macro-bot / guanlan / finance-mcp / stock-tweet-*）已从工作树移除，完整内容保留在 git 历史（重构前最后提交 `f5393b0`），去向与兼容说明见 [docs/MIGRATION_NOTES.md](docs/MIGRATION_NOTES.md)。

## License

MIT
