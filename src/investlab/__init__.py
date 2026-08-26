"""investlab — 本地优先的投资研究工作台。

模块总览：
- config        全部配置与 token 读取（仅环境变量 / .env，源码零密钥）
- netguard      出站请求安全校验（SSRF 防护）
- llm           OpenAI 兼容 LLM 客户端（Kimi 等）
- obsidian      Obsidian Vault 笔记写入
- datasources   行情 / K线 / 基本面 / 宏观 / 新闻数据源（多路回退 + TTL 缓存）
- search        互联网搜索聚合（DuckDuckGo 免费 + Tavily 可选）
- quant         技术指标 / 信号 / 回测 / 组合 / 赛道筛选
- reports       券商报告 PDF 解析（文本 + 图表图片提取 + 视觉模型结构化）
- analysis      每日简报 / 个股深度分析（UZI 风格双阶段流水线）
- tracks        投资赛道框架（对齐《AI 生产端普及》研究报告）
- api           FastAPI 本地服务（预留线上部署接口）
- cli           typer 命令行入口
"""

__version__ = "2.0.0"
