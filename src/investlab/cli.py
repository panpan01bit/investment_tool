"""investlab 命令行入口（typer）。

常用命令：
  investlab init-vault                 初始化 Obsidian Vault 结构
  investlab doctor                     检查配置/token/网络/Obsidian
  investlab daily                      跑今日听涛晨报
  investlab analyze 300308 [--search]  个股深度分析
  investlab signals 002837.SZ          技术信号
  investlab backtest 300308 --strategy sma_cross
  investlab screener optical-module    赛道筛选
  investlab report ingest <pdf>        收报告入库
  investlab report parse <rid>         解析报告（图表+摘要+Obsidian）
  investlab report list                报告列表
  investlab search "液冷 订单"          联网搜索
  investlab chat "光模块景气如何？"      基于简报+搜索追问
  investlab serve                      启动本地 API + 前端
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="investlab — 本地投资研究工作台", no_args_is_help=True,
                  add_completion=False)
report_app = typer.Typer(help="券商报告管理", no_args_is_help=True)
app.add_typer(report_app, name="report")
console = Console()


@app.command()
def init_vault():
    """初始化 Obsidian Vault 目录结构与首页。"""
    from investlab.obsidian.vault import new_vault

    v = new_vault()
    home = v.write_note(
        "Home.md",
        "# 观澜 · InvestLab\n\n"
        "- `10 听涛日报` — 每日晨报（自动生成）\n"
        "- `20 个股研究` — 个股深度分析（investlab analyze 自动写入）\n"
        "- `30 报告库` — 券商报告解析结果\n"
        "- `40 赛道研究` — AI 生产端主线赛道框架与跟踪\n"
        "- `50 组合/holdings.csv` — 持仓文件（symbol,name,quantity,cost_price,currency,category,run）\n",
        overwrite=True,
    )
    console.print(f"[green]Vault 已就绪[/] {v.path}")
    console.print(f"首页: {home}")


@app.command()
def doctor():
    """体检：配置、token、网络、Obsidian、依赖。"""
    import importlib.util

    from investlab.config import get_settings
    from investlab.netguard import is_safe_url

    s = get_settings()
    table = Table(title="investlab doctor")
    table.add_column("检查项")
    table.add_column("状态")
    table.add_column("说明")

    st = s.token_status()
    table.add_row("LLM Key", "[green]✓[/]" if st["llm"] else "[red]✗[/]",
                  s.llm_base_url)
    table.add_row("视觉模型", "[green]✓[/]" if st["vision"] else "[yellow]-[/]",
                  s.llm_vision_model or "未配置（图表理解降级）")
    table.add_row("Tushare", "[green]✓[/]" if st["tushare"] else "[yellow]-[/]",
                  "可选，基本面补充")
    table.add_row("Tavily", "[green]✓[/]" if st["tavily"] else "[yellow]-[/]",
                  "可选，搜索增强（DDG 免费可用）")
    table.add_row("FRED", "[green]✓[/]" if st["fred"] else "[yellow]-[/]", "可选")

    ak_ok = importlib.util.find_spec("akshare") is not None
    table.add_row("akshare", "[green]✓[/]" if ak_ok else "[red]✗[/]",
                  "" if ak_ok else 'pip install -e ".[cn]" 安装以启用A股数据')

    vault_ok = s.vault_path.expanduser().exists()
    table.add_row("Obsidian Vault", "[green]✓[/]" if vault_ok else "[yellow]-[/]",
                  str(s.vault_path))

    net_ok = False
    try:
        net_ok = is_safe_url("https://push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=43")
        resp_ok = net_ok and _probe_net()
    except Exception:
        resp_ok = False
    table.add_row("出站网络", "[green]✓[/]" if resp_ok else "[yellow]-[/]",
                  "东财行情可达" if resp_ok else "不可达（离线模式仍可本地分析）")

    console.print(table)


def _probe_net() -> bool:
    try:
        from investlab.datasources.quotes import get_quote

        q = get_quote("600519", use_cache=False)
        return bool(q.get("price"))
    except Exception:
        return False


@app.command()
def daily(fetch_news: bool = typer.Option(True, help="是否抓取新闻")):
    """生成今日听涛晨报（Obsidian + JSON）。"""
    from investlab.analysis.briefing import run_daily

    with console.status("生成晨报中…"):
        result = run_daily(fetch_news=fetch_news)
    _print_briefing_summary(result)


def _print_briefing_summary(result: dict):
    payload = result.get("payload") or {}
    pos = payload.get("positions") or []
    console.print(f"[bold green]晨报完成[/] {result['date']}")
    console.print(f"持仓条目: {len(pos)} | 新闻焦点: {len((payload.get('news') or {}).get('fresh') or [])}")
    console.print(f"JSON: {result['briefing_json']}")
    console.print(f"Obsidian: {result['obsidian_note']}")
    if not result.get("narrative_used_llm"):
        console.print("[yellow]提示：未配置 LLM，本轮为纯数据版晨报[/]")


@app.command()
def analyze(
    symbol: str = typer.Argument(..., help="标的，如 300308 / 00700.HK / NVDA"),
    use_search: bool = typer.Option(True, "--search/--no-search"),
):
    """个股深度分析 → Obsidian 研究笔记。"""
    from investlab.analysis.deep_analysis import analyze_symbol

    with console.status(f"深度分析 {symbol}…"):
        result = analyze_symbol(symbol, use_search=use_search)
    v = result.get("verdict") or {}
    console.print(f"[bold]结论[/]: {v.get('headline', '--')} (置信度 {v.get('confidence', '-')})")
    for x in (v.get("bull_case") or [])[:3]:
        console.print(f"  [green]+[/] {x}")
    for x in (v.get("bear_case") or [])[:3]:
        console.print(f"  [red]-[/] {x}")
    console.print(f"Obsidian: {result.get('obsidian_note')}")


@app.command()
def signals(symbol: str):
    """查看技术信号明细。"""
    from investlab.quant.signals import compute_signals

    rep = compute_signals(symbol).to_dict()
    table = Table(title=f"{symbol} 技术信号 {rep['ts']}")
    table.add_column("规则")
    table.add_column("方向")
    table.add_column("权重")
    table.add_column("理由")
    for r in rep["rules"]:
        d = "+" if r["direction"] > 0 else ("-" if r["direction"] < 0 else "·")
        table.add_row(r["name"], d, str(r["weight"]) if r["direction"] else "-", r["reason"])
    console.print(table)
    console.print(f"[bold]综合分 {rep['score']} · {rep['stance']}[/]")
    for g in rep.get("gaps", []):
        console.print(f"[yellow]缺口[/] {g}")


@app.command()
def backtest(
    symbol: str,
    strategy: str = typer.Option("sma_cross", help="sma_cross|rsi_reversion|breakout_20"),
    days: int = typer.Option(500),
):
    """轻量回测（净值曲线存 data/backtests）。"""
    from pathlib import Path

    from investlab.quant.backtest import run_backtest
    from investlab.utils.common import write_json

    res = run_backtest(symbol, strategy=strategy, days=days)
    if not res.get("ok"):
        console.print(f"[red]{res.get('error')}[/]")
        raise typer.Exit(1)
    m = res["metrics"]
    console.print(
        f"{symbol} {strategy}: 总收益 {m['total_return_pct']}% vs 基准 {m['bench_return_pct']}% | "
        f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']} | 最大回撤 {m['max_drawdown_pct']}% | "
        f"交易 {m['trades']} 次 胜率 {m['win_rate_pct']}%"
    )
    outdir = Path(__import__("investlab.config", fromlist=["get_settings"]).get_settings().data_dir) / "backtests"
    write_json(outdir / f"{symbol.replace('.', '_')}_{strategy}.json", res)


@app.command()
def screener(track_id: str):
    """赛道代表标的观察矩阵。"""
    from investlab.quant.screener import screen_track

    res = screen_track(track_id)
    if not res.get("ok"):
        console.print(f"[red]{res.get('error')}[/]")
        raise typer.Exit(1)
    meta = res["track"]
    console.print(f"[bold]{meta['name']}[/]（tier{meta['tier']}） TAM: {meta.get('tam') or '-'}")
    table = Table()
    for col in ("代码", "名称", "现价", "涨跌%", "信号分", "立场"):
        table.add_column(col)
    for r in res["rows"]:
        table.add_row(r["symbol"], r["name"], str(r["price"]),
                      str(r["change_pct"]), str(r["score"]), r["stance"])
    console.print(table)


@report_app.command("ingest")
def report_ingest(path: str):
    """PDF 入库（收进 data/reports/library/<hash>/）。"""
    from investlab.reports.pipeline import ingest_pdf

    rec = ingest_pdf(path)
    console.print(f"[green]已入库[/] id={rec['id']} 页数={rec['n_pages']} "
                  f"标题={ (rec.get('meta') or {}).get('title','') }")
    if rec.get("already_ingested"):
        console.print("(该报告此前已入库)")
    console.print(f"下一步: investlab report parse {rec['id']}")


@report_app.command("parse")
def report_parse(rid: str, vision: bool = typer.Option(True, "--vision/--no-vision")):
    """解析报告：图表提取+视觉结构化+摘要+写 Obsidian。"""
    from investlab.reports.pipeline import parse_report

    with console.status("解析中（图表较多时约需几分钟）…"):
        result = parse_report(rid, vision=vision)
    record = result["record"]
    n_fig = len(record.get("figures_meta") or [])
    summary = record.get("summary") or {}
    console.print(f"[green]解析完成[/] 图表 {n_fig} 张；"
                  f"一句话: {summary.get('one_liner') or '(未配置 LLM 或无文本)'}")
    console.print(f"Obsidian: {result['obsidian_note']}")


@report_app.command("list")
def report_list():
    from investlab.reports.pipeline import list_reports

    reports = list_reports()
    if not reports:
        console.print("(空) 用 ingest 添加 PDF")
        return
    table = Table(title="报告库")
    for col in ("ID", "日期", "机构", "标题", "评级"):
        table.add_column(col)
    for r in reports:
        table.add_row(r["id"], r.get("date") or "-", r.get("broker") or "-",
                      (r.get("title") or "")[:36], r.get("rating") or "-")
    console.print(table)


@app.command()
def search(query: str, max_results: int = typer.Option(8)):
    """联网搜索（DuckDuckGo 免费 / Tavily 可选）。"""
    from investlab.search import search as do_search

    hits = do_search(query, max_results=max_results, use_cache=False)
    if not hits:
        console.print("(无结果或无可用搜索源)")
        return
    for i, h in enumerate(hits, 1):
        console.print(f"{i}. [cyan]{h['badge']}[/] {h['title']}\n   [dim]{h['url']}[/dim]\n   {h['snippet'][:120]}")


@app.command()
def chat(question: str):
    """基于当日简报+联网搜索的追问。"""
    from investlab.analysis.chat import chat as do_chat

    result = do_chat(question)
    console.print(result["answer"])
    srcs = result.get("sources") or []
    if srcs:
        console.print("\n[dim]来源:[/]")
        for s in srcs:
            console.print(f"  [dim]{s.get('domain','')}: {s.get('title','')[:60]}[/dim]")


@app.command()
def serve(host: str = "", port: int = 0):
    """启动本地 API（默认 127.0.0.1:8300；web/dist 存在时同域托管前端）。"""
    import uvicorn

    from investlab.config import get_settings

    s = get_settings()
    h = host or s.api_host
    p = port or s.api_port
    console.print(f"[bold]investlab API[/] http://{h}:{p}  (docs: /docs)")
    uvicorn.run("investlab.api.main:app", host=h, port=p, reload=False)


if __name__ == "__main__":
    app()
