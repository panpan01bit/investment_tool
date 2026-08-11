# Kimi 专业数据源集成说明

本目录新增 Kimi 官方 `kimi-datasource` 插件（12 个专业数据源）接入，用于增强 `bot.py` 生成简报时的数据质量和分析深度。

## 数据源清单

| 数据源 | 用途 |
|--------|------|
| `stock_finance_data` | 同花顺 A 股/港股/美股财务、行情、公告、股东等 |
| `yahoo_finance` | Yahoo Finance 全球行情与财务 |
| `world_bank_open_data` | 世界银行 29000+ 宏观指标 |
| `tianyancha` | 企业工商/司法/知识产权 |
| `arxiv` | 学术论文预印本 |
| `scholar` | 学术文献检索 |
| `yuandian_law` | 法律法规 |
| `wind` | Wind 金融数据 |
| `imf` | IMF 数据 |
| `gildata` | 金数据 |
| `sec_edgar` | 美国 SEC 文件 |
| `sp_data` | S&P 数据 |

## 已集成到 macro-bot 的功能

- `kimi_datasource_client.py`：通用 MCP stdio 客户端封装。
- `kimi_datasource_enrichment.py`：简报增强模块，当前接入：
  - 全局：arXiv 最新研究摘要（LLM/量化/宏观方向）
  - 个股：同花顺 A 股基本信息（核心持仓 >= $10M）
- `bot.py`：
  - 宏观摘要部分自动追加 arXiv 研究增强
  - 个股分析 prompt 自动追加同花顺财务/股东信息

## 本地开发环境

### 1. 安装 Kimi CLI 与插件

```bash
# macOS
bash scripts/setup-kimi-datasource.sh

# 或手动
curl -fsSL https://kimi.com/kimi-code/install.sh | sh
# 下载并解压 kimi-datasource 插件到 ~/.kimi-code/plugins/managed/kimi-datasource
kimi login
```

### 2. 验证 token 刷新

Kimi `access_token` 仅约 15 分钟有效。macOS 已配置 `LaunchAgent`：

```bash
/Users/2/Library/LaunchAgents/com.kimi.token-refresh.plist
```

每 10 分钟执行一次刷新脚本，避免手动登录。

### 3. 验证数据源可用

```bash
cd /Users/2/Desktop/AI_Root/Coding/server-review/macro-bot
python kimi_datasource_client.py arxiv search_papers '{"query":"LLM finance","max_results":2,"file_path":"/tmp/arxiv_test.csv"}'
```

## 服务器部署流程

### 1. 推送代码后登录服务器

```bash
git pull origin main
```

### 2. 安装 Python 依赖

```bash
cd /www/wwwroot/macro-bot
/www/wwwroot/.venv311/bin/python -m pip install -r requirements.txt
```

`requirements.txt` 已新增 `mcp>=1.0.0`。

### 3. 安装 Node.js + Kimi CLI + 插件

以 root 执行：

```bash
cd /www/wwwroot/macro-bot
bash scripts/setup-kimi-datasource.sh
```

该脚本会：
- 安装 Node.js 22.x
- 安装 Kimi CLI
- 安装 `kimi-datasource` 插件
- 引导完成浏览器登录（首次）
- 安装 systemd timer 每 10 分钟刷新 token

### 4. 配置环境变量

复制示例并修改：

```bash
cp .env.example .env
vim .env
```

关键新增变量：

```
KIMI_DATASOURCE_NODE=/usr/bin/node
KIMI_DATASOURCE_SCRIPT=/root/.kimi-code/plugins/managed/kimi-datasource/bin/kimi-datasource.mjs
KIMI_CODE_OAUTH_HOST=https://auth.kimi.com
KIMI_CODE_BASE_URL=https://api.kimi.com/coding/v1
```

### 5. 重启 static-server

```bash
systemctl restart static-server
```

### 6. 验证 timer

```bash
systemctl status kimi-token-refresh.timer
systemctl status kimi-token-refresh.service
```

## 故障排查

### 报错：`Kimi Code access_token was rejected`

- 检查 `~/.kimi-code/credentials/kimi-code.json` 是否存在
- 运行 `kimi login` 重新登录
- 检查 refresh timer 是否正常运行：`systemctl status kimi-token-refresh.timer`

### 报错：`kimi-datasource plugin not found`

- 检查 `KIMI_DATASOURCE_SCRIPT` 路径是否正确
- 重新运行 `scripts/setup-kimi-datasource.sh`

### 报错：World Bank 查询超时

World Bank 全量数据查询常超过 MCP 30 秒限制，默认已不在 `enrich_macro_context()` 中调用。如需使用，请指定单个指标、单个国家、少量年份。

## 后续可扩展

- 接入 `yahoo_finance` 补充非 CH 市场财务数据
- 接入 `tianyancha` 做 A 股公司关联风险扫描
- 接入 `scholar` / `arxiv` 按持仓行业做深度研究增强
- 把 Kimi 数据源结果缓存到 Redis/磁盘，避免每次生成简报重复调用
