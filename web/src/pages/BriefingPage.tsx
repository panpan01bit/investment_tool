import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  api,
  type Briefing,
  type BriefingPosition,
  type BriefingsResp,
  type MacroResp,
} from '../lib/api';
import { errText, fmtNum, fmtPct, numClass } from '../lib/format';
import { pushToast } from '../lib/toast';
import {
  Card,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  NotePath,
  PageHead,
  Spinner,
} from '../components/ui';

const MACRO_LABELS: [string, keyof NonNullable<MacroResp['items']>][] = [
  ['制造业 PMI', 'pmi'],
  ['CPI 同比', 'cpi_yoy'],
  ['GDP 同比', 'gdp_yoy'],
  ['1 年期 LPR', 'lpr_1y'],
];

function normalizeNews(briefing: Briefing): { title: string; url: string; source?: string | null }[] {
  // API 里 news 是对象 {fresh:[], background:[], by_symbol:{}}；也兼容旧数组形态
  const raw = briefing.news as unknown;
  let list: unknown[] = [];
  if (Array.isArray(raw)) {
    list = raw;
  } else if (raw && typeof raw === 'object') {
    const obj = raw as { fresh?: unknown[]; background?: unknown[] };
    list = [...(obj.fresh ?? []), ...(obj.background ?? [])];
  }
  return list
    .map((n) => {
      if (typeof n === 'string') return { title: n, url: '' };
      const o = (n ?? {}) as { title?: string; link?: string; url?: string; source?: string };
      return {
        title: o.title || o.link || o.url || '(无标题)',
        url: o.link || o.url || '',
        source: o.source ?? null,
      };
    })
    .filter((n) => n.title);
}

export default function BriefingPage() {
  const [meta, setMeta] = useState<BriefingsResp | null>(null);
  const [selected, setSelected] = useState<string>('__latest');
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [running, setRunning] = useState(false);

  // Fallback macro (when briefing payload does not embed macro info)
  const [fallbackMacro, setFallbackMacro] = useState<MacroResp | null>(null);

  const load = async () => {
    setLoading(true);
    setErr('');
    try {
      const r = await api.briefings();
      setMeta(r);
      setBriefing(r.latest);
      setSelected('__latest');
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (briefing && !(briefing.macro || briefing.macro_text)) {
      api
        .macro()
        .then(setFallbackMacro)
        .catch(() => setFallbackMacro(null));
    } else {
      setFallbackMacro(null);
    }
  }, [briefing]);

  const onPick = async (val: string) => {
    setSelected(val);
    if (val === '__latest') {
      setBriefing(meta?.latest ?? null);
      return;
    }
    setLoading(true);
    setErr('');
    try {
      setBriefing(await api.briefing(val));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  };

  const runBriefing = async () => {
    setRunning(true);
    try {
      const r = await api.runBriefing();
      pushToast('success', `晨报已生成：${r.date}`, 6000);
      if (r.obsidian_note) pushToast('info', `笔记已写入：${r.obsidian_note}`, 9000);
      await load();
    } catch (e) {
      pushToast('error', `晨报生成失败：${errText(e)}`, 8000);
    } finally {
      setRunning(false);
    }
  };

  // 兼容两种形态：briefing.macro = {items:{...},text} 或直接是 items 对象
  const briefingMacro = briefing?.macro as
    | ({ items?: Record<string, number | null>; text?: string } & object)
    | null
    | undefined;
  const macroSrc: Record<string, unknown> =
    (briefingMacro?.items as Record<string, unknown>)
    ?? (briefingMacro as Record<string, unknown>)
    ?? (fallbackMacro?.items as Record<string, unknown>)
    ?? {};

  const sectors = briefing?.sectors ?? briefing?.macro?.sectors ?? fallbackMacro?.sectors ?? [];

  const positions: BriefingPosition[] = briefing?.positions ?? [];
  const news = briefing ? normalizeNews(briefing) : [];

  const pickDate = meta && selected !== '__latest' ? selected : null;

  return (
    <div className="page">
      <PageHead
        title="每日简报"
        subtitle={pickDate ? `正在查看历史简报 · ${pickDate}` : '宏观脉搏 × 持仓体检 × 新闻焦点'}
        actions={
          <>
            <select
              className="input"
              value={selected}
              onChange={(e) => void onPick(e.target.value)}
              aria-label="选择简报日期"
            >
              <option value="__latest">最新简报</option>
              {(meta?.dates ?? [])
                .slice()
                .sort((a, b) => b.localeCompare(a))
                .map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
            </select>
            <button className="btn btn-primary" onClick={() => void runBriefing()} disabled={running}>
              {running ? <Spinner size={14} /> : null}
              {running ? '生成中，约需 1–2 分钟…' : '生成今日晨报'}
            </button>
          </>
        }
      />

      {err && !briefing && <ErrorBanner message={err} onRetry={() => void load()} />}
      {!briefing && loading && !err && <LoadingBlock label="拉取简报数据…" />}
      {!briefing && !loading && !err && (
        <EmptyState>
          尚未生成任何晨报 — 点击右上角「生成今日晨报」开始第一次运行。
        </EmptyState>
      )}

      {briefing && (
        <>
          <div className="grid-2">
            <Card title="宏观一览 · MACRO ONE-PAGER">
              <div className="facts-grid">
                {MACRO_LABELS.map(([label, key]) => {
                  const v = (macroSrc as Record<string, unknown>)?.[key];
                  // PMI 是景气指数（50 荣枯线）不是百分比，其余为 %
                  const text = typeof v === 'number' ? (key === 'pmi' ? fmtNum(v, 1) : `${fmtNum(v, 2)}%`) : '—';
                  return (
                    <div className="fact" key={key}>
                      <div className="fact-k">{label}</div>
                      <div className="fact-v mono">{text}</div>
                    </div>
                  );
                })}
              </div>

              {sectors.length > 0 && (
                <div className="sector-strip">
                  {sectors.slice(0, 12).map((s) => (
                    <span
                      key={s.name}
                      className={`chip ${numClass(s.change_pct) === 'up' ? 'chip-up' : numClass(s.change_pct) === 'down' ? 'chip-down' : ''}`}
                    >
                      {s.name} <b className="mono">{fmtPct(s.change_pct)}</b>
                    </span>
                  ))}
                </div>
              )}

              {(briefingMacro?.text || fallbackMacro?.text) && (
                <p className="macro-text muted">{briefingMacro?.text || fallbackMacro?.text}</p>
              )}

              <NotePath path={briefing.obsidian_note} />
            </Card>

            <Card title={`新闻焦点 · NEWS (${news.length})`}>
              {news.length === 0 ? (
                <EmptyState>本期暂无新闻焦点</EmptyState>
              ) : (
                <ul className="news-list">
                  {news.map((n, i) =>
                    n.url ? (
                      <li key={i}>
                        <a href={n.url} target="_blank" rel="noopener noreferrer">
                          <IconInline />
                          <span className="news-title">{n.title}</span>
                          {n.source && <span className="news-src muted">{n.source}</span>}
                        </a>
                      </li>
                    ) : (
                      <li key={i}>
                        <span className="news-title plain">{n.title}</span>
                      </li>
                    ),
                  )}
                </ul>
              )}
            </Card>
          </div>

          <Card
            title={`持仓速览 · POSITIONS (${positions.length})`}
            extra={
              <Link className="btn btn-sm btn-ghost" to="/portfolio">
                管理组合 →
              </Link>
            }
          >
            {positions.length === 0 ? (
              <EmptyState>组合为空或尚未配置持仓</EmptyState>
            ) : (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>标的</th>
                      <th>名称</th>
                      <th className="num">数量</th>
                      <th className="num">现价</th>
                      <th className="num">涨跌幅</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((p) => (
                      <tr key={p.symbol}>
                        <td className="mono symbol-cell">
                          <Link to={`/research?s=${encodeURIComponent(p.symbol)}`}>{p.symbol}</Link>
                        </td>
                        <td>{p.name ?? p.symbol}</td>
                        <td className="num mono">{p.quantity != null ? fmtNum(p.quantity, 0) : '—'}</td>
                        <td className="num mono">{p.price != null ? fmtNum(p.price) : '—'}</td>
                        <td className={`num mono ${numClass(p.change_pct)}`}>
                          {fmtPct(p.change_pct ?? undefined)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

/** tiny inline arrow icon for news links */
function IconInline() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  );
}
