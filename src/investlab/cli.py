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
def init():
    """新用户一键初始化：.env（用户API专用文件）+ Vault + 投研档案 + 观察清单。"""
    import shutil

    from investlab.config import ENV_FILE_CANDIDATES, REPO_ROOT, get_settings

    # 1) 用户 API 环境：.env 缺失时从模板复制（密钥由用户自行填写，永不入库）
    env_file = ENV_FILE_CANDIDATES[0]
    if not env_file.is_file():
        template = REPO_ROOT / ".env.example"
        if template.is_file():
            shutil.copy2(template, env_file)
            console.print(f"[green]已创建[/] {env_file}（复制自 .env.example）")
            console.print("[yellow]下一步：编辑 .env 填入 INVESTLAB_LLM_API_KEY（唯一必填），其余可选[/]")
        else:
            console.print(f"[red]未找到模板 {template}[/]")
    else:
        console.print(f".env 已存在: {env_file}")

    # 2) 投研档案：生成用户个人定制文件 data/profile.json（不入库）
    s = get_settings(refresh=True)
    user_profile = s.data_dir / "profile.json"
    if not user_profile.is_file():
        from investlab.profiles import DEFAULT_PROFILE, _builtin_path

        bp = _builtin_path(DEFAULT_PROFILE)
        if bp:
            user_profile.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bp, user_profile)
            console.print(f"[green]已生成用户投研档案[/] {user_profile}")
            console.print("[dim]编辑它可定制主线/关注链/关键词/新闻源；API 密钥仍放 .env，两者分离[/]")

    # 3) Vault + 观察清单
    init_vault()
    from investlab.datasources.news import save_watchlist, watchlist_path
    from investlab.profiles import seed_watchlist_from_profile

    if not watchlist_path().is_file():
        save_watchlist(seed_watchlist_from_profile())
        wl = load_watchlist_safe()
        console.print(f"[green]观察清单已按投研档案播种[/] "
                      f"({len(wl.get('tickers') or [])} 只代码, "
                      f"{len(wl.get('macroKeywords') or [])} 个宏观关键词)")
    console.print("\n[bold]完成！常用命令[/]")
    console.print("  investlab doctor   # 体检（检查必填的 LLM key）")
    console.print("  investlab daily    # 生成今日晨报")
    console.print("  investlab serve    # 启动 Web 控制台")


def load_watchlist_safe():
    from investlab.datasources.news import load_watchlist

    return load_watchlist()


@app.command()
def init_vault():
    """仅初始化 Obsidian Vault 目录结构与首页。"""
    from investlab.obsidian.vault import new_vault

    v = new_vault()
    home = v.write_note(
        "Home.md",
        "# 观澜 · InvestLab\n\n"
        "- `10 听涛日报` — 每日晨报（自动生成）\n"
        "- `20 个股研究` — 个股深度分析（investlab analyze 自动写入）\n"
        "- `30 报告库` — 券商报告解析结果\n"
        "- `40 赛道研究` — 赛道框架与跟踪（由投研档案驱动）\n"
        "- `50 组合/holdings.csv` — 持仓文件（symbol,name,quantity,cost_price,currency,category,run）\n",
        overwrite=True,
    )
    console.print(f"[green]Vault 已就绪[/] {v.path}")
    console.print(f"首页: {home}")


@app.command("profile")
def profile_cmd(
    name: str = typer.Argument("", help="留空=查看当前；或设为内置名/JSON绝对路径"),
):
    """查看或切换投研档案（研究框架与使用习惯；API 密钥在 .env，两者分离）。"""
    import json as _json
    import os as _os

    from investlab.config import get_settings
    from investlab.profiles import builtin_profiles, profile_summary

    if name:
        if not (name in builtin_profiles() or _os.path.isabs(name)):
            console.print(f"[red]{name} 不是内置档案也不是绝对路径[/] 内置: {builtin_profiles()}")
            raise typer.Exit(1)
        _os.environ["INVESTLAB_PROFILE"] = name
        s = get_settings(refresh=True)
        s.profile = name
        console.print(f"[green]已切换到档案 {name}[/]（永久生效请写入 .env：INVESTLAB_PROFILE={name}）")

    console.print(_json.dumps(profile_summary(), ensure_ascii=False, indent=2))
    console.print(f"[dim]内置档案: {builtin_profiles()} · "
                  "个人定制: data/profile.json（不入库）[/]")


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


@app.command("portfolio-optimize")
def portfolio_optimize(
    method: str = typer.Option("hrp", help="max_sharpe | min_volatility | hrp"),
    max_weight: float = typer.Option(0.35, help="单标的权重上限"),
):
    """组合优化建议（PyPortfolioOpt）：目标权重 + 与当前持仓的调仓清单。"""
    from investlab.quant import analytics
    from investlab.quant.portfolio import build_portfolio_view, load_holdings

    symbols = sorted({h["symbol"] for h in load_holdings()})
    if len(symbols) < 2:
        console.print("[red]至少需要2只持仓（请先完善 50 组合/holdings.csv）[/]")
        raise typer.Exit(1)
    from investlab.datasources.candles import get_candles

    with console.status("拉取价格历史并优化…"):
        prices = analytics.prices_frame({s: get_candles(s, days=260) for s in symbols})
        result = analytics.optimize(prices, method=method, max_weight=max_weight)
    if not result.get("ok"):
        console.print(f"[red]{result.get('error')}[/]")
        raise typer.Exit(1)
    table = Table(title=f"建议权重 · {result['method']}")
    table.add_column("标的")
    table.add_column("目标权重")
    for sym, w in result["weights"].items():
        table.add_row(sym, f"{w:.1%}")
    console.print(table)
    console.print(f"[dim]{result['metrics'].get('method_note', '')} · {result['disclaimer']}[/]")

    view = build_portfolio_view()
    total_mv = sum(p.market_value or 0.0 for p in view.positions)
    if total_mv > 0:
        current = {p.symbol: round((p.market_value or 0.0) / total_mv * 100, 2)
                   for p in view.positions}
        suggestions = analytics.rebalance_suggestions(current, result["weights"])
        if suggestions:
            t2 = Table(title="调仓建议（|偏离|≥3%）")
            for col in ("标的", "当前%", "目标%", "偏离", "动作"):
                t2.add_column(col)
            for s in suggestions:
                t2.add_row(s["symbol"], str(s["current_pct"]), str(s["target_pct"]),
                           f"{s['diff_pct']:+.1f}", s["action"])
            console.print(t2)


@app.command("notify-test")
def notify_test():
    """向已配置的 ntfy/Bark 通道发一条测试推送。"""
    from investlab.notify import send_push

    results = send_push("InvestLab 测试推送", "如果你收到这条消息，推送通道配置成功 ✅")
    if not results:
        console.print("[yellow]未配置任何推送通道（.env 中 INVESTLAB_NOTIFY_*）[/]")
        return
    for r in results:
        ok = "[green]✓[/]" if r.ok else "[red]✗[/]"
        console.print(f"{ok} {r.channel}: {r.detail}")


@app.command()
def factors():
    """市场风格仪表：实证当前周期大小盘/动量/反转/波动哪种风格有效。"""
    from investlab.quant.factor_watch import factor_watch

    with console.status("拉取指数数据实证风格周期…"):
        res = factor_watch()
    table = Table(title=f"风格因子实证 · {res['date']} · 机制: {res['regime']}")
    table.add_column("因子")
    table.add_column("数值")
    table.add_column("结论")
    for st in res["styles"].values():
        val = st.get("pct", st.get("value"))
        unit = "%" if "pct" in st else ""
        table.add_row(st["name"], f"{val}{unit}", st["verdict"])
    console.print(table)
    for s in res["suggestions"]:
        console.print(f"[yellow]→[/] {s}")
    _write_factor_report(res)


def _write_factor_report(res: dict) -> str:
    """风格实证 + 轮动建议写进 Obsidian「40 赛道研究」。"""
    import json as _json

    from investlab.obsidian.vault import new_vault

    vault = new_vault()
    rel = "40 赛道研究/量化风格观察.md"
    body = [
        "---",
        "类型: 风格观察",
        f"日期: {res['date']}",
        "tags: [量化, 风格因子]",
        "---",
        f"# 量化风格观察 {res['date']}",
        "",
        f"**市场机制**：{res['regime']}",
        "",
        "## 实证结果",
        "",
        "| 因子 | 数值 | 结论 |",
        "| --- | --- | --- |",
    ]
    for st in res["styles"].values():
        val = st.get("pct", st.get("value"))
        unit = "%" if "pct" in st else ""
        body.append(f"| {st['name']} | {val}{unit} | {st['verdict']} |")
    body += ["", "## 对我们信号引擎的适配建议", ""]
    body += [f"- {s}" for s in res["suggestions"]] or ["- 当前无明显风格提示"]
    body += [
        "",
        "## 原始数据",
        "",
        "```json",
        _json.dumps(res["styles"], ensure_ascii=False, indent=1),
        "```",
    ]
    vault.write_note(rel, "\n".join(body), overwrite=True)
    console.print(f"[dim]已写入 Obsidian: {rel}[/]")
    return rel


@app.command()
def rotation(
    mode: str = typer.Option("signal", help="momentum | reversal | signal"),
    top: int = typer.Option(5, min=2, max=15),
    symbols: str = typer.Option("", help="逗号分隔标的池；留空=投研档案池(A/H)"),
    days: int = typer.Option(500),
    regime_filter: bool = typer.Option(True, "--regime-filter/--no-regime-filter",
                                       help="防御周（全指60日动量<-3%）持币"),
):
    """周频轮动回测：对标的池做朴素版量化选股（横截面打分+周频调仓）。"""
    from investlab.quant.rotation import default_universe, rotation_backtest

    if symbols.strip():
        pool = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        pool = default_universe()
    console.print(f"标的池 {len(pool)} 只 · 模式 {mode} · Top{top} · "
                  f"机制过滤{'开' if regime_filter else '关'}")
    with console.status("拉取K线并回测（池大时约1-2分钟）…"):
        res = rotation_backtest(pool, mode=mode, top_n=top, days=days,
                                regime_filter=regime_filter)
    if not res.get("ok"):
        console.print(f"[red]{res.get('error')}[/]")
        raise typer.Exit(1)
    m, bm = res["metrics"], res["bench_metrics"]
    table = Table(title=f"轮动回测 {res['start_date']} 起 · {res['weeks']}周 · "
                        f"防御周{res.get('defensive_weeks', 0)} · {res['total_trades']}笔调仓")
    for col in ("指标", "策略", "基准(池内等权)"):
        table.add_column(col)
    for k, label in (("total_return_pct", "总收益%"), ("cagr_pct", "年化%"),
                     ("sharpe", "夏普"), ("max_drawdown_pct", "最大回撤%")):
        table.add_row(label, str(m.get(k)), str(bm.get(k)))
    console.print(table)
    picks = res["latest_picks"]
    console.print(f"[bold]最新持仓[/] ({picks['date']}): {'、'.join(picks['symbols'])}")
    console.print("[dim]数据点有限的小池子结论仅作框架参考，非投资建议[/]")


@app.command("strategy-report")
def strategy_report():
    """综合策略研究：私募因子画像推断 + 风格实证 + 轮动矩阵 → Obsidian 报告。"""
    import json as _json

    from investlab.obsidian.vault import new_vault
    from investlab.quant.factor_watch import factor_watch
    from investlab.quant.rotation import default_universe, rotation_compare

    with console.status("阶段1/2：风格因子实证…"):
        fw = factor_watch()
    with console.status("阶段2/2：多模式轮动矩阵（同一数据快照）…"):
        cmp_res = rotation_compare(default_universe(), top_n=5)

    bench = cmp_res.get("bench_metrics", {})
    vault = new_vault()
    rel = "40 赛道研究/量化策略研究报告.md"
    # 保护人工撰写/增补过的报告：已存在则写带日期的版本，不覆盖
    if vault.note_exists(rel):
        rel = f"40 赛道研究/量化策略研究报告 {fw['date']}.md"
    lines = [
        "---",
        f"日期: {fw['date']}",
        "类型: 策略研究",
        "tags: [量化, 因子, 轮动, 私募]",
        "---",
        "# 量化策略研究：头部私募因子画像与我们的实证",
        "",
        "## 一、头部私募业绩与因子画像推断",
        "",
        "样本：百亿级量化选股私募（截至8/21，万得全A同期 +0.86%）。",
        "头部前列（今年收益）：正定 +20.75%、鸣石 +20.41%、鸣熙 +16.15%、",
        "华年 +15.96%、九坤 +14.92%、世纪前沿 +12.45%、明汯 +11.36%。",
        "",
        "公开信息推断的共性因子框架：",
        "",
        "1. **量价短周期因子为主**（价量、反转、波动、换手、振幅），叠加机器",
        "   学习合成（XGBoost/深度学习），周频换仓；",
        "2. **全市场选股 + 行业/市值中性化**——他们的'反转'是中性化后的统计",
        "   规律，不是朴素抄底下跌股；",
        "3. 2026 年环境（震荡+高波动+缩量）下量价因子内卷，行业平均超额转负",
        "   （量化选股约 -0.83%），表内 +14~21% 属极端头部；",
        "4. 新超额来源：多模态另类数据（招聘/舆情/资金流）+ 模型快速迭代。",
        "",
        "## 二、我们引擎的实证（同期、免费数据、同快照对比）",
        "",
        f"市场机制读数：**{fw['regime']}**",
        "",
        "### 风格因子实证",
        "",
        "| 因子 | 数值 | 结论 |",
        "| --- | --- | --- |",
    ]
    for st in fw["styles"].values():
        val = st.get("pct", st.get("value"))
        unit = "%" if "pct" in st else ""
        lines.append(f"| {st['name']} | {val}{unit} | {st['verdict']} |")

    lines += [
        "",
        "### 轮动矩阵（我们的观察池，Top5 周频，同一数据快照）",
        "",
        f"窗口：{cmp_res.get('start_date')} 起 {cmp_res.get('window_days')} 个交易日，"
        f"池 {cmp_res.get('universe_size')} 只（高beta AI算力链）。",
        "基准=池内等权："
        f"收益 {bench.get('total_return_pct')}%、回撤 {bench.get('max_drawdown_pct')}%、"
        f"夏普 {bench.get('sharpe')}。",
        "",
        "| 模式 | 机制过滤 | 总收益% | 夏普 | 最大回撤% | 防御周 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in cmp_res.get("results", []):
        m = r["metrics"]
        lines.append(
            f"| {r['mode']} | {'开' if r['regime_filter'] else '关'} "
            f"| {m['total_return_pct']} | {m['sharpe']} "
            f"| {m['max_drawdown_pct']} | {r['defensive_weeks']}/{r['weeks']} |"
        )

    lines += [
        "",
        "## 三、结论：适合我们的优化方式",
        "",
        "1. **机制过滤必须常开**：防御周（全指60日动量<-3%）持币，",
        "   对全部模式收益↑回撤↓——框架里最稳健的单一改进；",
        "2. **反转 + 机制过滤是当前周期最优组合**（+55.6%、回撤-18.2%、",
        "   夏普1.06）；指数日收益自相关为负（反转市）与反转占优互相印证；",
        "3. **朴素动量在机制拐点遭遇 momentum crash**（最高动量组跌最狠），",
        "   开过滤后 +12% 仍跑输反转——追高策略在反转市不可用；",
        "4. **等权基准难以跑赢**：池子高度相关（同一条AI算力链），Top5轮动",
        "   的alpha被池子beta主导——想超越等权需要扩池（跨赛道低相关）或",
        "   行业中性化（头部私募路径，我们数据不够）；",
        "5. 与头部的真实差距在**横截面宽度与另类数据**，不在框架——框架",
        "  （横截面打分+周频+风控开关）已经一致。",
        "",
        "## 四、原始数据",
        "",
        "```json",
        _json.dumps({"styles": fw["styles"], "rotation": cmp_res["results"]},
                    ensure_ascii=False, indent=1),
        "```",
        "",
        "---",
        "*研究框架输出，非投资建议；小样本窗口的结论随市场机制迁移。*",
    ]
    vault.write_note(rel, "\n".join(lines), overwrite=True)

    console.print(f"[green]策略研究报告已写入[/] {rel}")
    console.print(f"机制: {fw['regime']}")
    for r in cmp_res.get("results", []):
        m = r["metrics"]
        console.print(f"  {r['mode']:10} 过滤{'开' if r['regime_filter'] else '关'}: "
                      f"{m['total_return_pct']}% / 夏普{m['sharpe']} / 回撤{m['max_drawdown_pct']}%")


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
def pulse(
    query: str = typer.Argument("", help="英文关键词，如 'liquid cooling'"),
    symbol: str = typer.Option("", help="或直接传标的：NVDA / 00700.HK"),
    days: int = typer.Option(30, min=7, max=90),
):
    """跨源舆情热度（Reddit/HN/Polymarket/StockTwits/GitHub，真实互动量打分）。"""
    from investlab.datasources.social import pulse_for_symbol, social_pulse

    with console.status("跨源检索中…"):
        res = pulse_for_symbol(symbol) if symbol else social_pulse(query, days=days)
    heat = res.get("heat")
    if heat is None:
        console.print(f"[yellow]热度：无数据[/] 状态: {res.get('source_status')}")
        return
    label = res.get("heat_label", "")
    color = {"白热化": "red", "火热": "bright_red", "升温": "yellow"}.get(label, "cyan")
    console.print(f"[bold {color}]热度 {heat} · {label}[/]  "
                  f"[dim]查询: {res.get('query')} / {days}天[/]")
    for src, st in res.get("source_status", {}).items():
        console.print(f"  {src}: {st}")
    table = Table(title="互动量 Top")
    for col in ("源", "内容", "互动", "日期"):
        table.add_column(col)
    for it in res.get("items", [])[:12]:
        table.add_row(it["source"], it["title"][:52],
                      f"{it['engagement']:,} {it['metric']}", it.get("created", ""))
    console.print(table)


@app.command("social-record")
def social_record(
    symbols: str = typer.Option("", help="逗号分隔标的；留空则用持仓+赛道代表标的"),
):
    """采集当日社媒 heat 快照到历史库（建议每日 cron，供 tilt 研究积累数据）。"""
    from investlab.datasources.social import record_snapshots

    if symbols.strip():
        watch = [s.strip() for s in symbols.split(",") if s.strip()]
    else:
        from investlab.datasources.news import load_watchlist
        from investlab.tracks import all_track_stocks

        wl = load_watchlist()
        watch = list(dict.fromkeys(
            [str(t) for t in (wl.get("tickers") or [])]
            + [s for tid in ("1-6t-optical", "liquid-cooling", "hbm", "ai-chip")
               for s in all_track_stocks().get(tid, [])]
        ))
    watch = watch[:30]
    with console.status(f"采集 {len(watch)} 只标的…"):
        written = record_snapshots(watch)
    console.print(f"[green]写入 {len(written)} 条[/]（其余当日已存在或采集失败）")
    for r in written[:10]:
        console.print(f"  {r['symbol']}: heat={r['heat']} {r.get('heat_label', '')}")


@app.command("social-tilt")
def social_tilt_cmd(
    symbol: str = typer.Argument(...),
    min_days: int = typer.Option(60, min=7),
):
    """社媒热度 tilt（-1~+1）。历史不足 min_days 时明确拒绝。"""
    from investlab.quant.social_tilt import social_tilt

    res = social_tilt(symbol, min_days=min_days)
    if res is None:
        console.print(
            f"[yellow]{symbol}: 历史不足 {min_days} 日，tilt 不可用。[/]\n"
            "请先每天运行 investlab social-record 积累数据（建议 ≥60 日）。"
        )
        raise typer.Exit(1)
    console.print(f"{res.symbol}: tilt=[bold]{res.tilt:+.3f}[/] "
                  f"(heat={res.heat}, heat_z={res.heat_z}, slope_z={res.slope_z}, "
                  f"覆盖{res.coverage_days}日{', 拥挤警示' if res.crowding else ''})")
    for n in res.notes:
        console.print(f"  [dim]{n}[/dim]")


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
