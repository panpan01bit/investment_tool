"""本地 API 服务（FastAPI）。

默认只绑定 127.0.0.1（本机 GUI 前端使用）。预留线上部署：
- INVESTLAB_API_HOST=0.0.0.0 + INVESTLAB_AUTH_DISABLED=0 启用管理口令鉴权；
- 文件相关接口：报告ID/日期等参数严格格式校验；上传内容以服务端计算的
  哈希命名入库（路径不含用户输入成分）。
"""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..analysis.briefing import list_briefings, load_briefing, run_daily
from ..analysis.chat import chat as chat_answer
from ..analysis.deep_analysis import analyze_symbol, gather_facts
from ..config import get_settings
from ..datasources import macro as macromod
from ..datasources.candles import get_candles
from ..datasources.quotes import get_quote
from ..netguard import UnsafeURLError
from ..obsidian.vault import new_vault
from ..quant import analytics
from ..quant.backtest import STRATEGIES, run_backtest
from ..quant.portfolio import build_portfolio_view, save_portfolio_snapshot
from ..quant.screener import screen_portfolio_lenses, screen_track
from ..quant.signals import compute_signals
from ..reports.pipeline import (
    ingest_pdf_bytes,
    list_reports,
    parse_report,
    read_record,
)
from ..search import search as web_search
from ..tracks import load_taxonomy
from ..utils.common import setup_logging

log = setup_logging("investlab.api")

RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_REPORT_ID = re.compile(r"^[0-9a-f]{16}$")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def _valid_date(date: str) -> str:
    if not RE_DATE.match(date or ""):
        raise HTTPException(400, "日期格式须为 YYYY-MM-DD")
    return date


def _valid_rid(rid: str) -> str:
    if not RE_REPORT_ID.match(rid or ""):
        raise HTTPException(400, "报告ID格式非法")
    return rid


# ------------------------------------------------------------------ 鉴权


def require_auth(authorization: str | None = Header(default=None)) -> None:
    """线上模式（auth_disabled=False）才启用；本机默认放行。"""
    s = get_settings()
    if s.auth_disabled:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    expected = hashlib.sha256(
        f"{s.admin_username}:{s.admin_password}".encode()
    ).hexdigest()
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="令牌无效")


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    use_search: bool = True


class AnalyzeIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)


class BacktestIn(BaseModel):
    symbol: str
    strategy: str = "sma_cross"
    days: int = 500


def app_factory() -> FastAPI:
    s = get_settings()
    application = FastAPI(
        title="investlab API", version="2.0.0",
        description="本地投资研究工作台。线上部署预留：见 docs/ARCHITECTURE.md",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return application


app = app_factory()


# ------------------------------------------------------------------ 系统


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/settings/status", dependencies=[Depends(require_auth)])
def settings_status():
    s = get_settings()
    st = s.token_status()
    from .. import notify
    from ..profiles import profile_summary
    from ..reports import mineru_engine

    return {
        "version": "2.0.0",
        "tokens": {k: ("已配置" if v else "未配置") for k, v in st.items()},
        "vault_path": str(s.vault_path),
        "data_dir": str(s.data_dir),
        "mode": "local" if s.auth_disabled else "server-auth",
        "report_engine": s.report_engine,
        "mineru_available": mineru_engine.is_available(),
        "notify": notify.status(),
        "profile": profile_summary(),
    }


# ------------------------------------------------------------------ 简报 / 组合


@app.get("/api/briefings", dependencies=[Depends(require_auth)])
def briefings():
    return {"dates": list_briefings(), "latest": load_briefing()}


@app.get("/api/briefings/{date}", dependencies=[Depends(require_auth)])
def briefing_by_date(date: str):
    _valid_date(date)
    data = load_briefing(date)
    if not data:
        raise HTTPException(404, "简报不存在")
    return data


@app.post("/api/briefings/run", dependencies=[Depends(require_auth)])
def run_briefing_now():
    result = run_daily(use_cache=True, fetch_news=True)
    return {"ok": True, **{k: v for k, v in result.items() if k != "payload"}}


@app.get("/api/portfolio", dependencies=[Depends(require_auth)])
def portfolio_view():
    view = build_portfolio_view()
    out = view.to_dict()
    save_portfolio_snapshot(view)
    return out


@app.post("/api/portfolio", dependencies=[Depends(require_auth)])
async def portfolio_upload(rows: list[dict]):
    """前端保存新持仓（CSV 导入的行数组）。"""
    from ..quant.portfolio import write_holdings_csv

    clean = []
    for r in rows[:200]:
        raw_sym = str(r.get("symbol") or "").strip()
        # 标的代码白名单：6位数字/后缀、5位HK、纯字母美股
        sym = raw_sym.upper().replace(" ", "")
        if not (
            re.fullmatch(r"\d{6}(\.(SS|SZ|SH))?", sym)
            or re.fullmatch(r"\d{4,5}(\.HK)?", sym)
            or re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym)
        ):
            continue
        try:
            qty = float(r.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        clean.append({
            "symbol": sym,
            "name": str(r.get("name") or "")[:40].strip(),
            "quantity": qty,
            "cost_price": r.get("cost_price") or "",
            "currency": "CNY",
            "category": str(r.get("category") or "")[:30].strip(),
        })
    if not clean:
        raise HTTPException(400, "没有可用的持仓行")
    path = write_holdings_csv(clean)
    return {"ok": True, "saved_to": str(path)}


class OptimizeIn(BaseModel):
    method: str = "hrp"          # max_sharpe | min_volatility | hrp
    max_weight: float = Field(default=0.35, gt=0.01, le=1.0)


@app.post("/api/portfolio/optimize", dependencies=[Depends(require_auth)])
def portfolio_optimize(payload: OptimizeIn):
    """组合优化建议：拉持仓历史价格 → PyPortfolioOpt → 与当前权重对比。"""
    from ..quant.portfolio import load_holdings

    holdings = load_holdings()
    symbols = sorted({h["symbol"] for h in holdings})
    if len(symbols) < 2:
        raise HTTPException(400, "组合优化至少需要2只持仓（请先完善 holdings.csv）")
    candles_by_symbol = {s: get_candles(s, days=260) for s in symbols}
    prices = analytics.prices_frame(candles_by_symbol)
    result = analytics.optimize(prices, method=payload.method,
                                max_weight=payload.max_weight)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "优化失败"))
    current = {}
    total_mv = 0.0
    view = build_portfolio_view()
    for p in view.positions:
        mv = p.market_value or 0.0
        total_mv += mv
    if total_mv > 0:
        for p in view.positions:
            current[p.symbol] = round((p.market_value or 0.0) / total_mv * 100, 2)
    result["suggestions"] = analytics.rebalance_suggestions(current, result["weights"])
    result["current_weights"] = current
    return result


@app.get("/api/macro", dependencies=[Depends(require_auth)])
def macro_one_pager():
    m = macromod.get_macro_summary()
    return {**m, "text": macromod.format_one_pager(m)}


# ------------------------------------------------------------------ 个股 / 量化


@app.get("/api/quote/{symbol}", dependencies=[Depends(require_auth)])
def quote(symbol: str):
    q = get_quote(symbol)
    if not q.get("price"):
        raise HTTPException(404, f"无行情: {symbol}")
    return q


@app.get("/api/signals/{symbol}", dependencies=[Depends(require_auth)])
def signals(symbol: str):
    rep = compute_signals(symbol)
    d = rep.to_dict()
    if d["stance"] == "数据不足":
        raise HTTPException(404, "; ".join(d["gaps"]) or "数据不足")
    return d


@app.get("/api/social", dependencies=[Depends(require_auth)])
def social_pulse_route(
    query: str = "",
    symbol: str = "",
    days: int = 30,
):
    """跨源舆情热度（Reddit/HN/Polymarket/StockTwits/GitHub）。"""
    from ..datasources.social import social_pulse

    query = (query or "").strip()
    symbol = (symbol or "").strip()
    if not query and not symbol:
        raise HTTPException(400, "需要 query 或 symbol 之一")
    days = max(7, min(int(days), 90))
    if query and symbol:
        raise HTTPException(400, "query 与 symbol 只传其一")
    if symbol:
        from ..datasources.social import pulse_for_symbol

        return pulse_for_symbol(symbol)
    return social_pulse(query, days=days)


@app.post("/api/social/record", dependencies=[Depends(require_auth)])
async def social_record_route(symbols: list[str] | None = None):
    """采集当日 heat 快照（供定时任务/前端手动触发）。"""
    from ..datasources.social import record_snapshots

    if not symbols:
        from ..datasources.news import load_watchlist
        from ..tracks import all_track_stocks

        wl = load_watchlist()
        symbols = list(dict.fromkeys(
            [str(t) for t in (wl.get("tickers") or [])]
            + [s for tid in ("1-6t-optical", "liquid-cooling", "hbm", "ai-chip")
               for s in all_track_stocks().get(tid, [])]
        ))[:30]
    written = record_snapshots([str(s) for s in symbols][:30])
    return {"ok": True, "written": written}


@app.get("/api/social/tilt/{symbol}", dependencies=[Depends(require_auth)])
def social_tilt_route(symbol: str, min_days: int = 60):
    """社媒 tilt（历史不足返回 404 + 说明，绝不给数）。"""
    from ..quant.social_tilt import social_tilt

    res = social_tilt(symbol, min_days=max(7, min(int(min_days), 365)))
    if res is None:
        raise HTTPException(
            404,
            f"{symbol}: 社媒历史不足 {min_days} 日，tilt 不可用——"
            "请每日运行 investlab social-record 积累数据",
        )
    return res.to_dict()


@app.get("/api/research/facts/{symbol}", dependencies=[Depends(require_auth)])
def research_facts(symbol: str):
    return gather_facts(symbol, use_search=True)


@app.post("/api/research/analyze", dependencies=[Depends(require_auth)])
def research_analyze(payload: AnalyzeIn):
    try:
        return analyze_symbol(payload.symbol)
    except UnsafeURLError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("深度分析失败")
        raise HTTPException(500, f"分析失败: {exc}") from exc


@app.post("/api/backtest", dependencies=[Depends(require_auth)])
def backtest(payload: BacktestIn):
    if payload.strategy not in STRATEGIES:
        raise HTTPException(400, f"策略须为 {STRATEGIES}")
    days = max(120, min(int(payload.days), 1200))
    return run_backtest(payload.symbol, strategy=payload.strategy, days=days)


@app.get("/api/screen/track/{track_id}", dependencies=[Depends(require_auth)])
def screener_track(track_id: str):
    if not re.fullmatch(r"[a-z0-9\-]{2,40}", track_id or ""):
        raise HTTPException(400, "赛道ID格式非法")
    return screen_track(track_id)


@app.get("/api/screen/lenses", dependencies=[Depends(require_auth)])
def screener_lenses():
    return screen_portfolio_lenses()


@app.get("/api/candles/{symbol}", dependencies=[Depends(require_auth)])
def candles(symbol: str, days: int = 250):
    """K线数据（klinecharts 格式：timestamp 毫秒）。"""
    days = max(30, min(int(days), 1000))
    rows = get_candles(symbol, days=days)
    if not rows:
        raise HTTPException(404, f"无K线数据: {symbol}")
    out = []
    for r in rows:
        try:
            from datetime import datetime as _dt

            ts = int(_dt.strptime(str(r["date"])[:10], "%Y-%m-%d").timestamp() * 1000)
        except ValueError:
            continue
        out.append({
            "timestamp": ts,
            "open": r["open"], "high": r["high"], "low": r["low"],
            "close": r["close"], "volume": r.get("volume") or 0,
        })
    if not out:
        raise HTTPException(404, f"K线日期解析失败: {symbol}")
    return {"symbol": symbol, "count": len(out), "klines": out}


class TearSheetIn(BaseModel):
    symbol: str
    strategy: str = "sma_cross"
    days: int = 500


@app.post("/api/backtest/tearsheet", dependencies=[Depends(require_auth)])
def backtest_tearsheet(payload: TearSheetIn):
    """回测 + quantstats 绩效指标 + HTML 报告（存 Obsidian 附件目录）。"""
    if payload.strategy not in STRATEGIES:
        raise HTTPException(400, f"策略须为 {STRATEGIES}")
    days = max(120, min(int(payload.days), 1200))
    result = run_backtest(payload.symbol, strategy=payload.strategy, days=days)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "回测失败"))
    import pandas as pd

    curve = result.get("curve") or []
    equity = pd.Series(
        [c["strategy"] for c in curve],
        index=pd.to_datetime([c["date"] for c in curve]),
    )
    returns = equity.pct_change().dropna()
    metrics = analytics.performance_metrics(returns)
    report = {"ok": False}
    if metrics.get("ok"):
        vault = new_vault()
        out = vault.abs_path(
            "50 组合/绩效报告/"
            f"{payload.symbol.replace('.', '_')}_{payload.strategy}_tearsheet.html"
        )
        report = analytics.tear_sheet_html(
            returns, out, title=f"{payload.symbol} {payload.strategy}"
        )
        if report.get("ok"):
            report["obsidian_relpath"] = str(out.relative_to(vault.path))
    return {"backtest": {k: v for k, v in result.items() if k != "curve"},
            "quantstats": metrics, "report": report}


# ------------------------------------------------------------------ 推送


@app.post("/api/notify/test", dependencies=[Depends(require_auth)])
def notify_test():
    """向已配置通道发一条测试推送。"""
    from .. import notify

    results = notify.send_push("InvestLab 测试推送", "如果你收到这条消息，推送通道配置成功 ✅")
    if not results:
        raise HTTPException(400, "未配置任何推送通道（.env 中 INVESTLAB_NOTIFY_*）")
    return {"results": [r.__dict__ for r in results]}


# ------------------------------------------------------------------ 搜索 / 追问


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    max_results: int = Field(default=8, ge=1, le=20)


@app.post("/api/search", dependencies=[Depends(require_auth)])
def search_web(payload: SearchIn):
    hits = web_search(payload.query, max_results=payload.max_results)
    return {"query": payload.query, "hits": [dict(h) for h in hits]}


@app.post("/api/chat", dependencies=[Depends(require_auth)])
def chat_route(payload: ChatIn):
    return chat_answer(payload.question, use_search=payload.use_search)


# ------------------------------------------------------------------ 券商报告


@app.get("/api/reports", dependencies=[Depends(require_auth)])
def reports_list():
    return {"reports": list_reports()}


@app.get("/api/reports/{rid}", dependencies=[Depends(require_auth)])
def reports_detail(rid: str):
    rec = read_record(_valid_rid(rid))
    if not rec:
        raise HTTPException(404, "报告不存在")
    rec.pop("pdf_path", None)
    return rec


@app.post("/api/reports/upload", dependencies=[Depends(require_auth)])
async def upload_report(file: UploadFile = File(...)):  # noqa: B008 — FastAPI 惯用法
    """上传 PDF：内容哈希命名入库，路径不含任何用户输入成分。"""
    original_name = Path(file.filename or "report.pdf").name[:120]
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件过大（>30MB）")
    try:
        rec = ingest_pdf_bytes(data, original_name=original_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "record": {k: v for k, v in rec.items() if k != "pdf_path"},
            "next": f"POST /api/reports/{rec['id']}/parse"}


@app.post("/api/reports/{rid}/parse", dependencies=[Depends(require_auth)])
def parse_report_route(rid: str):
    try:
        result = parse_report(_valid_rid(rid),
                              vision=get_settings().report_vision_enabled)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return result


@app.get("/api/reports/{rid}/pdf", dependencies=[Depends(require_auth)])
def report_pdf(rid: str):
    rid = _valid_rid(rid)
    rec = read_record(rid)
    if not rec:
        raise HTTPException(404, "报告不存在")
    p = Path(rec["pdf_path"]).resolve()
    lib_root = get_settings().reports_library_dir.resolve()
    if lib_root not in p.parents or not p.is_file():   # 白名单目录内才下发
        raise HTTPException(403, "路径受限")
    return FileResponse(p, media_type="application/pdf", filename=f"{rid}.pdf")


# ------------------------------------------------------------------ 赛道 / Vault / 前端


@app.get("/api/tracks", dependencies=[Depends(require_auth)])
def tracks_all():
    return load_taxonomy()


@app.get("/api/vault/tree", dependencies=[Depends(require_auth)])
def vault_tree(limit: int = 60):
    limit = max(1, min(limit, 200))
    v = new_vault()
    out = []
    for base in ("10 听涛日报", "20 个股研究", "30 报告库"):
        for p in v.list_dir(base)[:limit]:
            out.append({"dir": base, "name": p.name,
                        "is_dir": p.is_dir(),
                        "relpath": p.relative_to(v.path).as_posix()})
    return {"root": str(v.path), "items": out}


# 打包后的前端（web/dist）由 API 同域服务；开发期走 Vite dev server。
# SPA 路由（/quant 等）刷新时回退到 index.html；静态资源仅从 dist 白名单目录下发。
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if WEB_DIST.is_dir():
    _ASSETS = WEB_DIST / "assets"
    if _ASSETS.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path.startswith("api/") or ".." in full_path:
            raise HTTPException(404, "接口不存在")
        candidate = (WEB_DIST / full_path).resolve()
        if str(candidate).startswith(str(WEB_DIST.resolve())) and candidate.is_file():
            return FileResponse(candidate)
        # 带扩展名的未知路径（.js/.json/伪文件）直接 404，不做 SPA 回退
        if "." in Path(full_path).name:
            raise HTTPException(404, "资源不存在")
        return FileResponse(WEB_DIST / "index.html", media_type="text/html")
