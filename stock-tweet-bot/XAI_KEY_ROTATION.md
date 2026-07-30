# xAI API Key 轮换操作手册

## 背景

之前的 xAI API key 曾写入 `stock-tweet-bot/config.yaml` 并进入仓库历史。当前本地仓库已清理（git 历史重写），但服务器原仓库和任何未同步分支仍可能暴露该 key。必须登录 xAI 控制台完成轮换。

## 影响范围

以下目录/脚本依赖 `XAI_API_KEY`：

- `/www/wwwroot/stock-tweet-bot/fetch.py`
- `/www/wwwroot/stock-tweet-grok-trader/grok_research.py`
- `/www/wwwroot/stock-tweet-grok-trader/grok_chat.py`
- `/www/wwwroot/stock-tweet-grok-trader/autotrader.py`
- `/www/wwwroot/stock-tweet-grok-trader/market_to_results.py`
- `/www/wwwroot/stock-tweet-grok-trader/pre-processing/*.py`
- `/www/wwwroot/stock-tweet-grok-trader/post-processing/*.py`
- `/www/wwwroot/stock-tweet-grok-trader/strategy/*.py`

因此轮换后必须同时更新两个项目的环境变量。

## 操作步骤

1. 登录 xAI 开发者控制台：https://console.x.ai
2. 进入 **API Keys** 页面。
3. 找到旧的 key：
   - 若记得 key 名称，直接删除。
   - 若不确定，建议删除所有曾用于 `stock-tweet-bot` / `stock-tweet-grok-trader` 的 key。
4. 创建新的 API key，记录名称（如 `si-pu-xai-prod-2026`）。
5. 在服务器上更新环境变量（不要写入配置文件）：
   ```bash
   # stock-tweet-bot
   cat > /www/wwwroot/stock-tweet-bot/.env <<'EOF'
   XAI_API_KEY=xai-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
   EOF

   # stock-tweet-grok-trader（如果它已经有 .env，追加/替换；否则新建）
   cat > /www/wwwroot/stock-tweet-grok-trader/.env <<'EOF'
   XAI_API_KEY=xai-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
   EOF
   ```
6. 如果通过 systemd/supervisor/crontab 运行，确保它们加载 .env：
   - crontab 已设置 `SHELL=/bin/bash` 和 `PATH`，但默认不会自动 source .env。
   - 推荐在 `run.sh` 里加入 `set -a; source /www/wwwroot/stock-tweet-bot/.env; set +a`。
   - 如果是 systemd 服务，执行 `systemctl restart <service>`。
7. 重新加载并验证（使用当前 venv 的 Python 3.11）：
   ```bash
   cd /www/wwwroot/stock-tweet-bot
   set -a; source /www/wwwroot/stock-tweet-bot/.env; set +a
   /www/wwwroot/.venv311/bin/python -c "import os; print('key present:', bool(os.getenv('XAI_API_KEY')))"
   /www/wwwroot/.venv311/bin/python fetch.py
   ```
8. 验证 grok-trader 模块：
   ```bash
   cd /www/wwwroot/stock-tweet-grok-trader
   set -a; source /www/wwwroot/stock-tweet-grok-trader/.env; set +a
   /www/wwwroot/.venv311/bin/python -c "import grok_research; print('import OK')"
   ```
9. 验证成功后，删除服务器上任何旧版 `config.yaml` 的备份或带 key 的文件：
   ```bash
   find /www/wwwroot -name 'config.yaml' -o -name '*.bak' -o -name '*.old' 2>/dev/null | xargs -r grep -l 'xai-' 2>/dev/null
   ```

## 本地代码改动

- `fetch.py` 现在优先读取 `XAI_API_KEY` 环境变量，覆盖配置文件中的值。
- `config.example.yaml` 不再包含真实 key，仅使用 `${XAI_API_KEY}` 占位。
- 新增 `.env.example` 供服务器参考。

## 验证清单

- [ ] 旧 key 已在 xAI 控制台删除
- [ ] 新 key 已设置到 `/www/wwwroot/stock-tweet-bot/.env`
- [ ] 新 key 已设置到 `/www/wwwroot/stock-tweet-grok-trader/.env`
- [ ] 运行 `fetch.py` 成功并输出推文摘要
- [ ] `grok_research.py` 可正常导入且无 key 报错
- [ ] 服务器上不存在含旧 key 的 `config.yaml` 备份或 `.env` 旧版本
- [ ] 如使用 systemd/supervisor，已重启服务
