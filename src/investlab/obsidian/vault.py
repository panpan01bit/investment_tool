"""Obsidian Vault 集成。

设计要点：
- 全部笔记用相对路径管理，Vault 根目录来自 INVESTLAB_OBSIDIAN_VAULT；
- 目录结构（首次 init 自动创建）：
    00 Inbox/                 快速捕捉
    10 听涛日报/YYYY-MM-DD.md   每日简报（原 Feishu 推送改为本地笔记）
    20 个股研究/<股票名-代码>.md 深度分析笔记
    30 报告库/<日期 券商 标题>/  券商报告：note.md + figures/ + tables/
    40 赛道研究/               赛道 taxonomy 与主线跟踪
    50 组合/holdings.csv       持仓
    90 Archive/
- 原子写入；文件名净化；frontmatter 由 pydantic dict 生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from ..utils.common import atomic_write_bytes, atomic_write_text, safe_filename, today_str

# Obsidian 常用目录（相对 Vault 根）
DIR_INBOX = "00 Inbox"
DIR_BRIEFINGS = "10 听涛日报"
DIR_RESEARCH = "20 个股研究"
DIR_REPORTS = "30 报告库"
DIR_TRACKS = "40 赛道研究"
DIR_PORTFOLIO = "50 组合"
DIR_ARCHIVE = "90 Archive"

VAULT_LAYOUT = [DIR_INBOX, DIR_BRIEFINGS, DIR_RESEARCH, DIR_REPORTS,
                DIR_TRACKS, DIR_PORTFOLIO, DIR_ARCHIVE]


def build_frontmatter(meta: dict) -> str:
    """dict → YAML frontmatter 字符串（含首尾 ---）。"""
    def fmt_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (list, tuple)):
            if not v:
                return "[]"
            return "\n" + "\n".join(f"  - {fmt_value(x)}" for x in v)
        if v is None:
            return '""'
        s = str(v).replace('"', "'")
        # 含特殊字符时加引号
        if any(c in s for c in ":#{}[]&*!|>%@`,"):
            return f'"{s}"'
        return s

    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {fmt_value(v)}")
    lines.append("---")
    return "\n".join(lines)


@dataclass
class Vault:
    """Obsidian Vault 的读写封装。"""

    root: Path | None = None
    rel_dirs: list[str] = field(default_factory=lambda: list(VAULT_LAYOUT))

    def __post_init__(self):
        if self.root is None:
            self.root = get_settings().vault_path.expanduser()

    # ------------------------------------------------------------- 结构

    @property
    def path(self) -> Path:
        return self.root

    def ensure_layout(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        for d in self.rel_dirs:
            (self.path / d).mkdir(parents=True, exist_ok=True)

    def abs_path(self, rel: str) -> Path:
        """把 Vault 内相对路径转绝对路径，并做越界检查。"""
        target = (self.path / rel).resolve()
        if not str(target).startswith(str(self.path.resolve())):
            raise ValueError(f"路径越界: {rel}")
        return target

    # ------------------------------------------------------------- 读写

    def note_exists(self, rel: str) -> bool:
        return self.abs_path(rel).is_file()

    def write_note(self, rel: str, content: str, *, overwrite: bool = False) -> Path:
        """写 Markdown 笔记（默认不覆盖已存在文件，重名自动加序号）。"""
        target = self.abs_path(rel)
        if not overwrite and target.exists():
            stem, suffix = target.stem, target.suffix or ".md"
            i = 2
            while (target.parent / f"{stem} {i}{suffix}").exists():
                i += 1
            target = target.parent / f"{stem} {i}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, content)
        return target

    def write_attachment(self, rel: str, data: bytes) -> Path:
        target = self.abs_path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, data)
        return target

    def read_note(self, rel: str) -> str | None:
        p = self.abs_path(rel)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    def list_dir(self, rel: str) -> list[Path]:
        d = self.abs_path(rel)
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if not p.name.startswith("."))

    # ------------------------------------------------------------- 高层 API

    def briefing_relpath(self, date_str: str = "") -> str:
        date_str = date_str or today_str()
        return f"{DIR_BRIEFINGS}/{date_str} 听涛晨报.md"

    def research_relpath(self, symbol: str, name: str = "") -> str:
        label = safe_filename(f"{name} {symbol}".strip() if name else symbol)
        return f"{DIR_RESEARCH}/{label}.md"

    def report_dir(self, date_str: str, broker: str, title: str) -> str:
        folder = safe_filename(f"{date_str} {broker} {title}", max_len=70)
        return f"{DIR_REPORTS}/{folder}"

    def holdings_path(self) -> Path:
        return self.abs_path(f"{DIR_PORTFOLIO}/holdings.csv")

    def append_track_log(self, track_id: str, line: str, date_str: str = "") -> None:
        """赛道跟踪：向 40 赛道研究/<track>.md 追加一行带时间戳的记录。"""
        date_str = date_str or today_str("%Y-%m-%d %H:%M")
        rel = f"{DIR_TRACKS}/{safe_filename(track_id)}.md"
        p = self.abs_path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"- `{date_str}` {line}\n")


def new_vault() -> Vault:
    v = Vault()
    v.ensure_layout()
    return v
