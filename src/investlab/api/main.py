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
from ..datasources.quotes import get_quote
from ..netguard import UnsafeURLError
from ..obsidian.vault import new_vault
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
    return {
        "version": "2.0.0",
        "tokens": {k: ("已配置" if v else "未配置") for k, v in st.items()},
        "vault_path": str(s.vault_path),
        "data_dir": str(s.data_dir),
        "mode": "local" if s.auth_disabled else "server-auth",
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
    from .quant.portfolio import write_holdings_csv

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
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
