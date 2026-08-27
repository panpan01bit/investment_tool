"""通知推送：ntfy（可自托管）与 Bark（iOS），晨报完成后的本地化"飞书替代"。

配置（全部可选，配哪个用哪个）：
  INVESTLAB_NOTIFY_NTFY_TOPIC   ntfy 主题名，如 investlab-panpan
  INVESTLAB_NOTIFY_NTFY_SERVER  自托管服务器，默认 https://ntfy.sh
  INVESTLAB_NOTIFY_BARK_URL     Bark 设备地址，如 https://api.day.app/yourKey
  INVESTLAB_NOTIFY_ALLOW_PRIVATE=1  允许推送自建内网服务器（显式放行 netguard）

安全：出站统一走 netguard 校验；allow_private 仅对 notify 通道生效且需显式开启。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import get_settings
from .utils.common import setup_logging

log = setup_logging("investlab.notify")


@dataclass
class NotifyResult:
    channel: str
    ok: bool
    detail: str = ""


def _channels() -> list[tuple[str, str, str]]:
    """返回 [(channel, url, extra_headers_json)] 已配置的推送通道。"""
    s = get_settings()
    out = []
    if s.notify_ntfy_topic:
        base = (s.notify_ntfy_server or "https://ntfy.sh").rstrip("/")
        out.append(("ntfy", f"{base}/{s.notify_ntfy_topic}", ""))
    if s.notify_bark_url:
        out.append(("bark", s.notify_bark_url.rstrip("/"), ""))
    return out


def send_push(title: str, body: str, *, allow_private: bool | None = None) -> list[NotifyResult]:
    """向全部已配置通道推送；未配置任何通道返回空列表。"""
    import requests

    settings = get_settings()
    allow = settings.notify_allow_private if allow_private is None else allow_private
    results = []
    for channel, url, _ in _channels():
        try:
            if allow:
                from .netguard import UnsafeURLError, validate_url

                try:
                    url = validate_url(url)
                except UnsafeURLError:
                    pass  # 显式允许内网自建服务器
            if channel == "ntfy":
                resp = requests.post(
                    url,
                    data=body.encode("utf-8"),
                    headers={"Title": title.encode("utf-8"), "Tags": "chart_with_upwards_trend"},
                    timeout=10,
                )
            else:  # bark: GET https://host/<key>/<title>/<body>
                from urllib.parse import quote

                resp = requests.get(
                    f"{url}/{quote(title, safe='')}/{quote(body, safe='')}",
                    timeout=10,
                )
            ok = resp.status_code in (200, 201)
            results.append(NotifyResult(channel, ok, f"HTTP {resp.status_code}"))
        except Exception as exc:
            results.append(NotifyResult(channel, False, str(exc)[:120]))
            log.debug("推送失败 %s: %s", channel, exc)
    for r in results:
        log.info("notify[%s] ok=%s %s", r.channel, r.ok, r.detail)
    return results


def notify_briefing(payload: dict, note_path: str) -> list[NotifyResult]:
    """晨报完成后的默认推送内容。"""
    positions = payload.get("positions") or []
    fresh = ((payload.get("news") or {}).get("fresh")) or []
    macro_text = (payload.get("macro") or {}).get("text", "")
    date = payload.get("date", "")
    title = f"听涛晨报 {date} 已生成"
    body = (
        f"持仓 {len(positions)} 只 · 焦点新闻 {len(fresh)} 条\n"
        f"{(macro_text or '').splitlines()[0][:60] if macro_text else ''}\n"
        f"Obsidian: {note_path}"
    )
    return send_push(title, body)


def status() -> dict:
    """doctor/API 展示用。"""
    ch = _channels()
    return {
        "configured": [c for c, _, _ in ch],
        "ntfy_topic": bool(get_settings().notify_ntfy_topic),
        "bark": bool(get_settings().notify_bark_url),
        "allow_private": bool(get_settings().notify_allow_private),
    }
