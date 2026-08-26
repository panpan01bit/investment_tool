# Obsidian 集成指南

## 1. 建立你的投研 Vault

方式 A（推荐，独立 Vault）：

```bash
investlab init-vault      # 默认在 INVESTLAB_OBSIDIAN_VAULT 指定的路径建结构
open -a Obsidian          # Obsidian → 打开文件夹作为库 → 选择该目录
```

方式 B（已有常用 Vault）：把 `.env` 中 `INVESTLAB_OBSIDIAN_VAULT` 指到现有 Vault 根目录，`init-vault` 会**只新增**下列子目录，不动你的其他笔记。

## 2. 目录契约

```
<Vault>/
├── Home.md                 # 入口页（自动生成）
├── 00 Inbox/
├── 10 听涛日报/             # investlab daily 输出，YYYY-MM-DD 听涛晨报.md
├── 20 个股研究/             # investlab analyze <symbol> 输出
├── 30 报告库/
│   ├── Index.md            # 报告目录表（自动维护）
│   └── 2026-07-28 华泰证券 xxx深度跟踪/
│       ├── xxx深度跟踪.md    # 图表数据化结果 + 摘要 + ![](figures/…)
│       └── figures/fig_01_p2.png
├── 40 赛道研究/             # 赛道跟踪追加记录
└── 50 组合/
    └── holdings.csv        # 持仓文件 ★ 程序与人工都可编辑
```

## 3. 持仓文件格式（holdings.csv）

```csv
symbol,name,quantity,cost_price,currency,category,run
300308.SZ,中际旭创,1000,138.5,CNY,光模块,Y
002837.SZ,英维克,1500,32.1,CNY,液冷,Y
NVDA,英伟达,50,110,USD,AI芯片,N
```

- `run=N` 的行不参与每日分析；
- `category` 会显示在晨报的“赛道”列；
- 旧 macro-bot 的 `holdings.xlsx` 放到数据目录（默认 `data/`）下会首次运行时自动迁移为 CSV。

## 4. Dataview 用法示例

报告库总览（装 Dataview 插件）：

````
```dataview
TABLE 券商, 评级, 目标价, 日期
FROM "30 报告库"
WHERE 类型 = "券商报告"
SORT 日期 DESC
```
````

个股研究最新结论：

````
```dataview
TABLE 代码, 技术分, 更新时间
FROM "20 个股研究"
WHERE 类型 = "个股研究"
SORT 更新时间 DESC
LIMIT 20
```
````

## 5. 日常动线建议

| 场景 | 动作 |
| --- | --- |
| 早上 | `investlab daily` → 读 `10 听涛日报` 当日晨报 |
| 盘中 | Web 控制台 `量化信号` 页扫赛道矩阵；`investlab chat` 追问 |
| 收到研报 | PDF 拖给 `investlab report ingest` → `parse --vision`；去 Obsidian 里核对图表数据表 |
| 决策前 | `investlab analyze <代码>` 生成多空卡片；结合信号分与技术规则 |
| 周末 | Dataview 浏览报告库；更新 taxonomy 感想进 `40 赛道研究` |
