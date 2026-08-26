"""组合分析：读取 Obsidian 里的 holdings.csv，联动行情计算持仓视图。

CSV 列（保持与旧 holdings.xlsx 兼容的核心列）：
    symbol,name,quantity,cost_price,currency,category,run
run=Y 才参与分析；category 为自由文本赛道标签。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from ..datasources import quotes as qmod
from ..datasources.symbols import normalize, split_symbol_column
from ..obsidian.vault import new_vault
from ..utils.common import setup_logging, write_json

log = setup_logging("investlab.portfolio")

COST_MIN_ABS = 1e-9


@dataclass
class Position:
    symbol: str
    name: str = ""
    quantity: float = 0.0
    cost_price: float | None = None
    currency: str = "CNY"
    category: str = ""
    price: float | None = None
    change_pct: float | None = None
    market_value: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    weight_pct: float | None = None
    quote_source: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class PortfolioView:
    positions: list[Position] = field(default_factory=list)
    total_value_cny: float | None = None
    total_pnl_cny: float | None = None
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "positions": [p.to_dict() for p in self.positions],
            "total_value_cny": self.total_value_cny,
            "total_pnl_cny": self.total_pnl_cny,
            "skipped": self.skipped,
        }


def load_holdings() -> list[dict]:
    """读 holdings.csv → raw rows。文件缺失返回 []。"""
    path = new_vault().holdings_path()
    if not path.is_file():
        # 兼容旧 macro-bot 的 xlsx：若存在且未转换过则自动迁移一次
        migrated = _try_migrate_legacy_xlsx(path)
        if not migrated:
            return []
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sym_raw = (row.get("symbol") or row.get("代码") or "").strip()
            if not sym_raw:
                continue
            run_flag = (row.get("run") or row.get("Run") or "Y").strip().upper()
            if run_flag and run_flag != "Y":
                continue
            sym, name_from_sym = split_symbol_column(sym_raw)
            name = (row.get("name") or row.get("名称") or name_from_sym or "").strip()
            try:
                qty = float(row.get("quantity") or row.get("数量") or 0)
                cost = _opt_float(row.get("cost_price") or row.get("成本价"))
            except ValueError:
                log.warning("持仓行解析失败: %s", row)
                continue
            if qty == 0:
                continue
            rows.append(
                {
                    "symbol": normalize(sym),
                    "name": name,
                    "quantity": qty,
                    "cost_price": cost,
                    "currency": (row.get("currency") or row.get("币种") or "").strip() or "CNY",
                    "category": (row.get("category") or row.get("分类") or "").strip(),
                }
            )
    return rows


def _opt_float(v):
    try:
        f = float(str(v).replace(",", ""))
        return None if abs(f) < COST_MIN_ABS else f
    except (TypeError, ValueError):
        return None


def build_portfolio_view(*, use_cache: bool = True) -> PortfolioView:
    rows = load_holdings()
    view = PortfolioView(skipped=[])
    fx = _fx_rates()
    values: list[float] = []
    pnls: list[float] = []
    for r in rows:
        pos = Position(**r)
        quote = qmod.get_quote(pos.symbol, use_cache=use_cache)
        if quote and quote.get("price"):
            pos.price = quote["price"]
            pos.change_pct = quote.get("change_pct")
            pos.quote_source = quote.get("source", "")
            pos.currency = quote.get("currency", pos.currency) or pos.currency
        else:
            view.skipped.append(f"{pos.symbol}: 无行情")
        rate = fx.get(pos.currency.upper(), 1.0)
        if pos.price is not None:
            pos.market_value = round(pos.quantity * pos.price * rate, 2)
            values.append(pos.market_value)
        if pos.price is not None and pos.cost_price:
            pos.pnl = round((pos.price - pos.cost_price) * pos.quantity * rate, 2)
            pos.pnl_pct = round((pos.price / pos.cost_price - 1) * 100, 2)
            pnls.append(pos.pnl)
        view.positions.append(pos)

    total = sum(values) or None
    for p in view.positions:
        if p.market_value is not None and total:
            p.weight_pct = round(p.market_value / total * 100, 2)
    view.total_value_cny = round(sum(values), 2) if values else None
    view.total_pnl_cny = round(sum(pnls), 2) if pnls else None
    return view


def save_portfolio_snapshot(view: PortfolioView) -> str:
    out = get_settings().data_dir / "portfolio_last.json"
    write_json(out, view.to_dict())
    return str(out)


# ------------------------------------------------------------------ FX 与旧表迁移


def _fx_rates() -> dict[str, float]:
    """简化汇率表；后续可接在线汇率。"""
    return {"CNY": 1.0, "HKD": 0.92, "USD": 6.65}


def write_holdings_csv(rows: list[dict]) -> str:
    """前端/CLI 上传新持仓时落盘到 Obsidian（代码白名单校验）。"""
    import re

    path = new_vault().holdings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    def valid_symbol(sym: str) -> bool:
        return bool(re.fullmatch(
            r"\d{6}(\.(SS|SZ|SH))?|\d{4,5}(\.HK)?|[A-Z][A-Z0-9.\-]{0,9}",
            sym,
        ))

    import io

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["symbol", "name", "quantity", "cost_price", "currency", "category", "run"],
    )
    writer.writeheader()
    for r in rows:
        sym = normalize(str(r.get("symbol") or "").strip())
        if not sym or not valid_symbol(sym.upper()):
            log.warning("忽略非法标的代码: %r", r.get("symbol"))
            continue
        writer.writerow({
            "symbol": sym,
            "name": str(r.get("name", ""))[:40],
            "quantity": r.get("quantity", ""),
            "cost_price": r.get("cost_price", ""),
            "currency": r.get("currency", "CNY"),
            "category": str(r.get("category", ""))[:30],
            "run": "Y",
        })
    path.write_text(buf.getvalue(), encoding="utf-8")
    return str(path)


def _try_migrate_legacy_xlsx(target_csv) -> bool:
    """若数据目录/Vault 根/仓库根有旧版 holdings.xlsx，则转换为 CSV（一次性）。"""
    settings = get_settings()
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        settings.data_dir / "holdings.xlsx",
        new_vault().path / "holdings.xlsx",
        repo_root / "holdings.xlsx",
    ]
    for src in candidates:
        if not src.is_file():
            continue
        try:
            import pandas as pd

            df = pd.read_excel(src, sheet_name=0)
            cols = {c.lower(): c for c in df.columns}
            code_col = next((cols[c] for c in ("code", "代码", "symbol") if c in cols), None)
            if not code_col:
                return False
            qty_col = next((cols[c] for c in ("quantity", "数量", "shares") if c in cols), None)
            cost_col = next((cols[c] for c in ("cost price", "成本价", "cost", "均价") if c in cols), None)
            name_col = next((cols[c] for c in ("名称", "股票名称", "name") if c in cols), None)
            cat_col = next((cols[c] for c in ("分类", "category", "组合") if c in cols), None)
            run_col = next((cols[c] for c in ("run",) if c in cols), None)
            rows = []
            for _, r in df.iterrows():
                sym, name_guess = split_symbol_column(str(r[code_col]))
                if not sym:
                    continue
                run_val = str(r[run_col]).strip().upper() if run_col else "Y"
                if run_val and run_val != "Y":
                    continue
                qty = float(r[qty_col]) if qty_col is not None else 0.0
                if qty == 0:
                    continue
                rows.append({
                    "symbol": normalize(sym),
                    "name": str(r[name_col]) if name_col else name_guess,
                    "quantity": qty,
                    "cost_price": float(r[cost_col]) if cost_col is not None else "",
                    "currency": "",
                    "category": str(r[cat_col]) if cat_col else "",
                })
            if rows:
                target_csv.parent.mkdir(parents=True, exist_ok=True)
                write_holdings_csv(rows)
                return True
        except Exception as exc:
            log.warning("迁移旧 holdings 失败: %s", exc)
    return False
