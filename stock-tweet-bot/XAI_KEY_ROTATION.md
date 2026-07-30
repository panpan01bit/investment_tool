# xAI API Key 轮换操作手册

## 背景
之前的 xAI API key 曾写入 `stock-tweet-bot/config.yaml` 并进入仓库历史。当前本地仓库已清理（git 历史重写），但服务器原仓库和任何未同步分支仍可能暴露该 key。必须登录 xAI 控制台完成轮换。

## 操作步骤

1. 登录 xAI 开发者控制台：https://console.x.ai
2. 进入 **API Keys** 页面。
3. 找到旧的 key：
   - 若记得 key 名称，直接删除。
   - 若不确定，建议删除所有曾用于 `stock-tweet-bot` 的 key。
4. 创建新的 API key，记录名称（如 `stock-tweet-bot-prod`）。
5. 在服务器上更新环境变量（不要写入配置文件）：
   ```bash
   # 编辑 /www/wwwroot/stock-tweet-bot/.env
   XAI_API_KEY=your_new_key_here
   ```
6. 重新加载环境变量并验证：
   ```bash
   cd /www/wwwroot/stock-tweet-bot
   source .env
   python3 fetch.py
   ```
7. 验证成功后，删除服务器上任何旧版 `config.yaml` 的备份或带 key 的文件。

## 本地代码改动
- `fetch.py` 现在优先读取 `XAI_API_KEY` 环境变量，覆盖配置文件中的值。
- `config.example.yaml` 不再包含真实 key，仅使用 `${XAI_API_KEY}` 占位。
- 新增 `.env.example` 供服务器参考。

## 验证清单
- [ ] 旧 key 已在 xAI 控制台删除
- [ ] 新 key 已设置到服务器环境变量
- [ ] `python3 fetch.py` 运行成功并输出推文摘要
- [ ] 服务器上不存在含旧 key 的 `config.yaml` 备份
