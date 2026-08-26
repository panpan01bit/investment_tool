import { useEffect, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, type ResearchResult, type SignalRule } from '../lib/api';
import { errText, fmtNum, fmtPct, numClass } from '../lib/format';
import {
  Card,
  ConfidenceBadge,
  CopyBtn,
  EmptyState,
  ErrorBanner,
  PageHead,
  RulesList,
} from '../components/ui';

export default function ResearchPage() {
  const [params] = useSearchParams();
  const [input, setInput] = useState(params.get('s') ?? '');
  const [phase, setPhase] = useState<'idle' | 'running' | 'done'>('idle');
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [err, setErr] = useState('');
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const s = params.get('s');
    if (s) setInput(s);
  }, [params]);

  // Ticker while a deep analysis is running.
  useEffect(() => {
    if (phase !== 'running') return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  const run = async () => {
    const sym = input.trim();
    if (!sym || phase === 'running') return;
    setPhase('running');
    setResult(null);
    setErr('');
    try {
      const r = await api.researchAnalyze(sym);
      setResult(r);
      setPhase('done');
    } catch (e) {
      setErr(errText(e));
      setPhase('idle');
    }
  };

  return (
    <div className="page">
      <PageHead
        title="个股研究"
        subtitle="深度尽调：行情 + 技术 + 基本面 + 赛道定位（约需 10–60 秒）"
      />

      <div className="search-row">
        <input
          className="input search-input mono"
          placeholder="输入代码：300308 / 00700.HK / NVDA"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void run();
          }}
          disabled={phase === 'running'}
        />
        <button
          className="btn btn-primary"
          onClick={() => void run()}
          disabled={phase === 'running' || !input.trim()}
        >
          {phase === 'running' ? '分析中…' : '开始深度分析'}
        </button>
      </div>

      {phase === 'running' && (
        <div className="card progress-card">
          <div className="progress-line">
            <span>正在进行深度研究…</span>
            <span className="mono">{elapsed}s</span>
          </div>
          <div className="progress-track">
            <div className="progress-indeterminate" />
          </div>
          <p className="muted small">
            首次分析需要依次拉取行情快照、历史K线、财务指标、赛道定位与新闻证据，通常 10–60 秒完成，请勿关闭页面。
          </p>
        </div>
      )}

      {err && !result && <ErrorBanner message={`深度分析失败：${err}`} onClose={() => setErr('')} />}

      {!result && phase === 'idle' && !err && (
        <EmptyState>
          输入标的代码后点击「开始深度分析」，将生成多空逻辑、关键数据与仓位建议。
        </EmptyState>
      )}

      {result && <ResearchView r={result} />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Result rendering                                                    */
/* ------------------------------------------------------------------ */

function ResearchView({ r }: { r: ResearchResult }) {
  const v = r.verdict;
  const quote = r.facts?.quote ?? {};
  const tech = (r.facts?.technical ?? {}) as Record<string, unknown>;
  const fun = (r.facts?.fundamentals ?? {}) as Record<string, unknown>;

  const num = (val: unknown): number | undefined =>
    typeof val === 'number' && Number.isFinite(val) ? val : undefined;
  const str = (val: unknown): string | undefined =>
    typeof val === 'string' && val.trim() ? val : undefined;

  /* facts grid */
  const cells: { k: string; v: ReactNode; cls?: string }[] = [];
  cells.push({ k: '最新价', v: quote.price != null ? fmtNum(quote.price) : '—' });
  cells.push({
    k: '涨跌幅',
    v: fmtPct(quote.change_pct ?? undefined),
    cls: numClass(quote.change_pct ?? null),
  });
  const tScore = num(tech.score);
  cells.push({
    k: '技术得分',
    v: tScore != null ? (tScore > 0 ? `+${tScore}` : String(tScore)) : '—',
    cls: numClass(tScore),
  });
  const stance = str(tech.stance);
  if (stance) cells.push({ k: '技术研判', v: stance });
  const pe = num(fun.pe);
  if (pe != null) cells.push({ k: '市盈率 PE', v: `${fmtNum(pe)}x` });
  const pb = num(fun.pb);
  if (pb != null) cells.push({ k: '市净率 PB', v: `${fmtNum(pb)}x` });
  const roe = num(fun.roe);
  if (roe != null) cells.push({ k: 'ROE', v: `${fmtNum(roe)}%` });
  for (const key of ['rsi14', 'ma20', 'ma200'] as const) {
    const val = num(tech[key]);
    const label = key === 'rsi14' ? 'RSI(14)' : key.toUpperCase();
    if (val != null) cells.push({ k: label, v: fmtNum(val) });
  }
  const dist52 = num(tech.dist_52w_high_pct);
  if (dist52 != null)
    cells.push({ k: '距52周高点', v: fmtPct(dist52), cls: dist52 < -15 ? 'down' : '' });

  const fits = v?.thesis_fit?.fits;

  return (
    <>
      {/* headline banner */}
      <section className="hero card">
        <div className="hero-main">
          <div className="hero-sym">
            <span className="mono strong">{r.symbol}</span>
            {quote.name && <span className="muted">{quote.name}</span>}
            <span className={`mono ${numClass(quote.change_pct ?? null)}`}>
              {fmtNum(quote.price ?? null)} {fmtPct(quote.change_pct ?? undefined)}
            </span>
          </div>
          <h2 className="hero-headline">{v.headline}</h2>
        </div>
        <div className="hero-side">
          <ConfidenceBadge confidence={v.confidence} />
          {v.thesis_fit && (
            <span
              className={`chip ${fits ? 'chip-fit' : 'chip-off'}`}
              title={v.thesis_fit.comment}
            >
              {fits ? '契合主线' : '偏离主线'}
              <em>{v.thesis_fit.comment}</em>
            </span>
          )}
        </div>
      </section>

      <div className="grid-2">
        <Card title="多头逻辑 · BULL CASE">
          {v.bull_case?.length ? (
            <ul className="bullets bull">
              {v.bull_case.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          ) : (
            <EmptyState>暂无多头论据</EmptyState>
          )}
        </Card>
        <Card title="空头与风险 · BEAR CASE">
          {v.bear_case?.length ? (
            <ul className="bullets bear">
              {v.bear_case.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          ) : (
            <EmptyState>暂无空头论据</EmptyState>
          )}
        </Card>
      </div>

      <Card title="关键数据 · KEY FACTS">
        <div className="facts-grid facts-wide">
          {cells.map((c) => (
            <div className="fact" key={c.k}>
              <div className="fact-k">{c.k}</div>
              <div className={`fact-v mono${c.cls ? ` ${c.cls}` : ''}`}>{c.v}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid-2-1">
        <Card title="技术信号 · RULES">
          <RulesList rules={Array.isArray(tech.rules) ? (tech.rules as unknown as SignalRule[]) : []} />
        </Card>

        <div className="stack">
          <Card title="赛道定位 · TRACKS">
            {r.facts?.tracks?.length ? (
              <div className="chips-row">
                {r.facts.tracks.map((t) => (
                  <span className="chip track-chip" key={`${t.tier}-${t.id}`}>
                    <b>[{t.tier}]</b> {t.name}
                    {t.maturity && <em>{t.maturity}</em>}
                  </span>
                ))}
              </div>
            ) : (
              <EmptyState>未匹配到二级/三级赛道</EmptyState>
            )}
          </Card>

          <Card title="仓位建议 · POSITION ADVICE">
            <div className="advice-grid">
              <div className="fact">
                <div className="fact-k">方向判断</div>
                <div className={`fact-v advice-stance ${numClass(stanceTone(v.position_advice?.stance))}`}>
                  {v.position_advice?.stance ?? '—'}
                </div>
              </div>
              <div className="fact">
                <div className="fact-k">建议入场区间</div>
                <div className="fact-v mono">{v.position_advice?.entry_zone ?? '—'}</div>
              </div>
              <div className="fact">
                <div className="fact-k">失效条件 / 止损位</div>
                <div className="fact-v mono small">{v.position_advice?.invalidation ?? '—'}</div>
              </div>
            </div>
          </Card>
        </div>
      </div>

      {r.obsidian_note && (
        <Card title="Obsidian 笔记归档">
          <div className="notepath">
            <code className="mono notepath-text">{r.obsidian_note}</code>
            <CopyBtn text={r.obsidian_note} />
          </div>
        </Card>
      )}
    </>
  );
}

/** Map stance words to +/- tone (red bullish / green bearish). */
function stanceTone(stance?: string): number {
  if (!stance) return 0;
  if (/买入|看多|加仓|强烈|积极/.test(stance)) return 1;
  if (/卖出|看空|减仓|回避|谨慎/.test(stance)) return -1;
  return 0;
}
