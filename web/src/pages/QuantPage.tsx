import { useEffect, useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import {
  api,
  type BacktestResp,
  type BacktestStrategy,
  type Candle,
  type LensesResp,
  type OptimizeResp,
  type QuantstatsMetrics,
  type Signal,
  type TearSheetResp,
  type TrackDetail,
} from '../lib/api';
import { errText, fmtInt, fmtNum, fmtPct, numClass } from '../lib/format';
import { pushToast } from '../lib/toast';
import CandleChart from '../components/CandleChart';
import { Card, EmptyState, ErrorBanner, LoadingBlock, PageHead, RulesList, ScoreBar, Spinner, Tabs, WarnBanner } from '../components/ui';

const TABS = [
  { key: 'lenses', label: '赛道总览' },
  { key: 'signal', label: '单标的信号' },
  { key: 'bt', label: '回测' },
];

const STRATEGY_LABELS: Record<BacktestStrategy, string> = {
  sma_cross: '双均线交叉 (SMA Cross)',
  rsi_reversion: 'RSI 超卖回归',
  breakout_20: '20 日动量突破',
};

export default function QuantPage() {
  const [tab, setTab] = useState('lenses');
  return (
    <div className="page">
      <PageHead title="量化信号" subtitle="赛道筛选 · 单标的规则打分 · 策略回测" />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'lenses' && <LensTab />}
      {tab === 'signal' && <SignalTab />}
      {tab === 'bt' && <BacktestTab />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab 1: 赛道总览                                                     */
/* ------------------------------------------------------------------ */

type SortKey = 'symbol' | 'price' | 'change_pct' | 'score';
type SortDir = 1 | -1;

function LensTab() {
  const [lenses, setLenses] = useState<LensesResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const [activeTrack, setActiveTrack] = useState('');
  const [detail, setDetail] = useState<TrackDetail | null>(null);
  const [dLoading, setDLoading] = useState(false);
  const [dErr, setDErr] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('score');
  const [sortDir, setSortDir] = useState<SortDir>(-1);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        setLenses(await api.screenLenses());
        setErr('');
      } catch (e) {
        setErr(errText(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const openTrack = async (id: string) => {
    setActiveTrack(id);
    setDLoading(true);
    setDErr('');
    try {
      setDetail(await api.screenTrack(id));
    } catch (e) {
      setDErr(errText(e));
      setDetail(null);
    } finally {
      setDLoading(false);
    }
  };

  const rows = useMemo(() => {
    if (!detail?.rows) return [];
    const key = sortKey;
    return [...detail.rows].sort((a, b) => {
      let cmp = 0;
      if (key === 'symbol') cmp = a.symbol.localeCompare(b.symbol);
      else {
        const av = Number((a as unknown as Record<string, unknown>)[key] ?? -Infinity);
        const bv = Number((b as unknown as Record<string, unknown>)[key] ?? -Infinity);
        cmp = av - bv;
      }
      return cmp * sortDir;
    });
  }, [detail, sortKey, sortDir]);

  const lensGroups = useMemo(() => {
    const entries = Object.entries(lenses?.lenses ?? {});
    const aKey = entries.find(([k]) => k.startsWith('A'))?.[0];
    const bKey = entries.find(([k]) => k.startsWith('B'))?.[0];
    return {
      // When no "A"-prefixed group exists, fall back to the first declared group.
      a: ((aKey ? lenses?.lenses[aKey] : Object.values(lenses?.lenses ?? {})[0]) ?? []) as LensesResp['lenses'][string],
      b: (bKey ? lenses?.lenses[bKey] : []) as LensesResp['lenses'][string],
    };
  }, [lenses]);

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(k);
      setSortDir(k === 'symbol' ? 1 : -1);
    }
  };

  if (loading) return <LoadingBlock label="加载赛道图谱…" />;
  if (err) return <ErrorBanner message={err} onRetry={() => window.location.reload()} />;

  return (
    <>
      <Card title="Top10 赛道排行">
        {(lenses?.top_ranking ?? []).length === 0 ? (
          <EmptyState>暂无排名数据</EmptyState>
        ) : (
          <ol className="rank-list">
            {(lenses?.top_ranking ?? []).map((r) => (
              <li key={`${r.rank}-${r.id}`}>
                <button className="link-btn" onClick={() => void openTrack(r.id)}>
                  <span className="mono rank-num">#{r.rank}</span>
                  <span>{r.track}</span>
                </button>
                <span className="muted mono small">{r.id}</span>
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card title="A 类赛道 · 卖铲子（Capex 链）">
        <LensGrid list={lensGroups.a} activeId={activeTrack} onOpen={(id) => void openTrack(id)} />
      </Card>

      <Card title="B 类赛道 · 用铲子（生产率提升）">
        <LensGrid list={lensGroups.b} activeId={activeTrack} onOpen={(id) => void openTrack(id)} />
      </Card>

      {(lenses?.verification_metrics ?? []).length > 0 && (
        <Card title="验证指标清单">
          <ul className="check-list">
            {(lenses?.verification_metrics ?? []).map((m) => (
              <li key={m}>
                <span className="check-mark">✓</span>
                {m}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {(activeTrack || dErr) && (
        <div id="track-matrix">
          {dErr && <ErrorBanner message={`赛道 ${activeTrack} 加载失败：${dErr}`} onClose={() => setDErr('')} />}
          {dLoading && <LoadingBlock label={`拉取 ${activeTrack} 成员矩阵…`} />}
          {detail && !dLoading && (
            <Card
              title={`成员矩阵 · ${detail.track.name}${detail.track.tier ? ` (${detail.track.tier})` : ''}`}
            >
              {rows.length === 0 ? (
                <EmptyState>该赛道暂无成员数据</EmptyState>
              ) : (
                <div className="tbl-wrap">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th className="sortable" onClick={() => toggleSort('symbol')}>
                          标的 {sortMark('symbol', sortKey, sortDir)}
                        </th>
                        <th>名称</th>
                        <th className="num sortable" onClick={() => toggleSort('price')}>
                          现价 {sortMark('price', sortKey, sortDir)}
                        </th>
                        <th className="num sortable" onClick={() => toggleSort('change_pct')}>
                          涨跌 {sortMark('change_pct', sortKey, sortDir)}
                        </th>
                        <th className="sortable" onClick={() => toggleSort('score')}>
                          得分 {sortMark('score', sortKey, sortDir)}
                        </th>
                        <th>评级</th>
                        <th>缺口</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.symbol}>
                          <td className="mono">{r.symbol}</td>
                          <td>{r.name}</td>
                          <td className="num mono">{fmtNum(r.price)}</td>
                          <td className={`num mono ${numClass(r.change_pct)}`}>{fmtPct(r.change_pct)}</td>
                          <td style={{ minWidth: 160 }}>
                            <ScoreBar score={r.score} />
                          </td>
                          <td>
                            <span className={`chip ${numClass(r.score) === 'up' ? 'chip-up' : numClass(r.score) === 'down' ? 'chip-down' : ''}`}>
                              {r.stance || '—'}
                            </span>
                          </td>
                          <td className="gaps-cell muted small">{(r.gaps ?? []).join('；') || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          )}
        </div>
      )}
    </>
  );
}

function sortMark(key: SortKey, active: SortKey, dir: SortDir): string {
  if (key !== active) return '';
  return dir === 1 ? ' ↑' : ' ↓';
}

function LensGrid({
  list,
  activeId,
  onOpen,
}: {
  list: LensesResp['lenses'][string];
  activeId: string;
  onOpen: (id: string) => void;
}) {
  if (!list || list.length === 0) return <EmptyState>暂无该类赛道</EmptyState>;
  return (
    <div className="lens-grid">
      {list.map((t) => (
        <button
          key={t.id}
          className={`lens-card${activeId === t.id ? ' active' : ''}`}
          onClick={() => onOpen(t.id)}
        >
          <div className="lens-name">{t.name}</div>
          <div className="mono lens-id muted small">{t.id}</div>
          <dl className="lens-meta">
            <div>
              <dt>TAM</dt>
              <dd className="mono">{t.tam ?? '—'}</dd>
            </div>
            <div>
              <dt>CAGR</dt>
              <dd className="mono">{t.cagr ?? '—'}</dd>
            </div>
            <div>
              <dt>成熟度</dt>
              <dd>{t.maturity ?? '—'}</dd>
            </div>
          </dl>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab 2: 单标的信号                                                   */
/* ------------------------------------------------------------------ */

const IND_LABELS: Record<string, string> = {
  rsi14: 'RSI(14)',
  ma20: 'MA20',
  ma200: 'MA200',
  dist_52w_high_pct: '距52周高点',
};

function indLabel(k: string): string {
  return IND_LABELS[k] ?? k.replace(/_/g, ' ').toUpperCase();
}

function indValue(v: unknown, k: string): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return typeof v === 'string' && v ? v : '—';
  if (k.endsWith('_pct') || k.endsWith('_yoy')) return fmtPct(v, 2, false);
  if (/^(ma|rsi)/i.test(k)) return fmtNum(v);
  return fmtNum(v, 2);
}

function SignalTab() {
  const [sym, setSym] = useState('');
  const [sig, setSig] = useState<Signal | null>(null);
  const [klines, setKlines] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const query = async () => {
    const s = sym.trim();
    if (!s) return;
    setLoading(true);
    setErr('');
    try {
      setSig(await api.signals(s));
      // K线独立容错：拿不到只隐藏图，不影响信号
      try {
        setKlines((await api.candles(s, 250)).klines);
      } catch {
        setKlines([]);
      }
    } catch (e) {
      setSig(null);
      setKlines([]);
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  };

  const gaugeOpt = useMemo<EChartsOption | null>(() => {
    if (!sig || !Number.isFinite(sig.score)) return null;
    const s = Math.max(-100, Math.min(100, sig.score));
    return {
      backgroundColor: 'transparent',
      series: [
        {
          type: 'gauge',
          min: -100,
          max: 100,
          startAngle: 205,
          endAngle: -25,
          radius: '95%',
          center: ['50%', '58%'],
          splitNumber: 4,
          axisLine: {
            lineStyle: {
              width: 16,
              color: [
                [0.25, '#16a34a'],
                [0.42, '#4ade80'],
                [0.5, '#334155'],
                [0.58, '#f87171'],
                [0.75, '#ef4444'],
                [1, '#b91c1c'],
              ],
            },
          },
          pointer: {
            length: '62%',
            width: 5,
            itemStyle: { color: '#e2e8f0' },
          },
          axisTick: { distance: -18, length: 4, lineStyle: { color: '#0b0f14', width: 1 } },
          splitLine: { distance: -20, length: 10, lineStyle: { color: '#0b0f14', width: 2 } },
          axisLabel: {
            distance: -32,
            color: '#64748b',
            fontSize: 10,
            formatter: (v: unknown) => String(Math.round(Number(v))),
          },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, '58%'],
            fontSize: 28,
            fontWeight: 700,
            fontFamily: 'SF Mono, Menlo, monospace',
            color: s >= 0 ? '#ef4444' : '#22c55e',
            formatter: (v: unknown) => {
              const n = Math.round(Number(v));
              return n > 0 ? `+${n}` : String(n);
            },
          },
          data: [{ value: s }],
        },
      ],
    } as EChartsOption;
  }, [sig]);

  return (
    <>
      <div className="search-row">
        <input
          className="input search-input mono"
          placeholder="输入代码：300308 / 00700.HK / NVDA"
          value={sym}
          onChange={(e) => setSym(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void query()}
          disabled={loading}
        />
        <button className="btn btn-primary" onClick={() => void query()} disabled={loading || !sym.trim()}>
          {loading ? '计算中…' : '查询信号'}
        </button>
      </div>

      {err && <ErrorBanner message={`信号查询失败：${err}`} onClose={() => setErr('')} />}

      {!sig && !err && loading && <LoadingBlock label="计算技术指标与规则得分…" />}
      {!sig && !err && !loading && (
        <EmptyState>输入标的代码后点击「查询信号」，查看 -100 ~ +100 的多空打分。</EmptyState>
      )}

      {sig && (
        <>
          <div className="grid-signal">
            <Card title={`综合评分 · ${sig.symbol}`} extra={<span className="chip">{sig.stance}</span>}>
              {gaugeOpt ? (
                <ReactECharts option={gaugeOpt} notMerge style={{ height: 300 }} opts={{ renderer: 'canvas' }} />
              ) : (
                <EmptyState>无有效得分</EmptyState>
              )}
            </Card>

            <div className="stack">
              <Card title="核心指标">
                <div className="facts-grid">
                  {Object.entries(sig.indicators ?? {}).map(([k, v]) => (
                    <div className="fact" key={k}>
                      <div className="fact-k">{indLabel(k)}</div>
                      <div className="fact-v mono">{indValue(v, k)}</div>
                    </div>
                  ))}
                  {Object.keys(sig.indicators ?? {}).length === 0 && <EmptyState>暂无指标</EmptyState>}
                </div>
                {sig.ts && <p className="muted small ts-line">更新于 {sig.ts}</p>}
              </Card>

              {sig.gaps?.length > 0 && (
                <WarnBanner>
                  数据缺口：{sig.gaps.join('；')} — 相关维度未参与打分，请谨慎参考。
                </WarnBanner>
              )}
            </div>
          </div>

          <Card title="规则明细">
            <RulesList rules={sig.rules} />
          </Card>

          {klines.length > 0 && (
            <Card title={`K线 · ${sig.symbol}（近${klines.length}个交易日）`}>
              <CandleChart klines={klines} />
            </Card>
          )}
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Tab 3: 回测                                                         */
/* ------------------------------------------------------------------ */

function BacktestTab() {
  const [sym, setSym] = useState('');
  const [strategy, setStrategy] = useState<BacktestStrategy>('sma_cross');
  const [days, setDays] = useState(500);
  const [resp, setResp] = useState<BacktestResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [tearsheet, setTearsheet] = useState<TearSheetResp | null>(null);
  const [tsLoading, setTsLoading] = useState(false);

  const run = async () => {
    const s = sym.trim();
    if (!s) return;
    setLoading(true);
    try {
      const r = await api.backtest({ symbol: s, strategy, days: Math.max(30, Math.floor(days) || 250) });
      if (r.ok) pushToast('success', `回测完成：${s} · ${STRATEGY_LABELS[strategy]}`);
      setResp(r);
      setTearsheet(null);
    } catch (e) {
      pushToast('error', `回测失败：${errText(e)}`, 8000);
    } finally {
      setLoading(false);
    }
  };

  const runTearsheet = async () => {
    const s = sym.trim();
    if (!s) return;
    setTsLoading(true);
    try {
      const r = await api.backtestTearsheet({ symbol: s, strategy, days: Math.max(120, Math.floor(days) || 250) });
      setTearsheet(r);
      if (r.quantstats.ok && r.report.ok) {
        pushToast('success', '绩效报告已生成并保存到 Obsidian「50 组合/绩效报告」');
      } else if (!r.quantstats.ok) {
        pushToast('error', r.quantstats.error ?? '绩效计算失败', 8000);
      }
    } catch (e) {
      pushToast('error', `绩效报告失败：${errText(e)}`, 8000);
    } finally {
      setTsLoading(false);
    }
  };

  const lineOpt = useMemo<EChartsOption | null>(() => {
    const curve = resp?.curve;
    if (!curve || curve.length === 0) return null;
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: '#111827', borderColor: '#334155', textStyle: { color: '#e5e7eb' } },
      legend: { top: 0, textStyle: { color: '#9ca3af' }, icon: 'roundRect', itemWidth: 12, itemHeight: 3 },
      grid: { left: 52, right: 18, top: 34, bottom: 34 },
      xAxis: {
        type: 'category',
        data: curve.map((p) => p.date),
        axisLabel: { color: '#64748b', fontSize: 10 },
        axisLine: { lineStyle: { color: '#334155' } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#64748b', fontSize: 10 },
        splitLine: { lineStyle: { color: '#1c2735', type: 'dashed' } },
      },
      series: [
        {
          name: '策略净值',
          type: 'line',
          data: curve.map((p) => p.strategy),
          showSymbol: false,
          lineStyle: { width: 2, color: '#3b82f6' },
          itemStyle: { color: '#3b82f6' },
          emphasis: { focus: 'series' },
        },
        {
          name: '基准',
          type: 'line',
          data: curve.map((p) => p.benchmark),
          showSymbol: false,
          lineStyle: { width: 1.4, color: '#94a3b8', type: 'dashed' },
          itemStyle: { color: '#94a3b8' },
          emphasis: { focus: 'series' },
        },
      ],
    };
  }, [resp]);

  const m = resp?.metrics;
  const trades = resp?.trade_list ?? [];

  return (
    <>
      <Card title="回测参数">
        <div className="bt-controls">
          <input
            className="input mono"
            placeholder="代码，如 300308"
            value={sym}
            onChange={(e) => setSym(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void run()}
            disabled={loading}
          />
          <select
            className="input"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as BacktestStrategy)}
            disabled={loading}
          >
            {(Object.keys(STRATEGY_LABELS) as BacktestStrategy[]).map((s) => (
              <option key={s} value={s}>
                {STRATEGY_LABELS[s]}
              </option>
            ))}
          </select>
          <label className="days-label muted small">
            回看天数
            <input
              className="input mono"
              type="number"
              min={30}
              step={30}
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              disabled={loading}
            />
          </label>
          <button className="btn btn-primary" onClick={() => void run()} disabled={loading || !sym.trim()}>
            {loading && <Spinner size={14} />}
            {loading ? '回测中…' : '运行回测'}
          </button>
          <button className="btn" onClick={() => void runTearsheet()} disabled={tsLoading || !sym.trim()}>
            {tsLoading && <Spinner size={14} />}
            {tsLoading ? '生成中…' : '绩效报告 (quantstats)'}
          </button>
        </div>
      </Card>

      {tearsheet && (
        <Card title="quantstats 绩效指标">
          {tearsheet.quantstats.ok ? (
            <>
              <div className="stat-row stats-wrap">
                <div className="stat"><div className="stat-label">年化 CAGR</div><div className="stat-value mono">{fmtPct(tearsheet.quantstats.cagr_pct)}</div></div>
                <div className="stat"><div className="stat-label">夏普</div><div className="stat-value mono">{fmtNum(tearsheet.quantstats.sharpe)}</div></div>
                <div className="stat"><div className="stat-label">索提诺</div><div className="stat-value mono">{fmtNum(tearsheet.quantstats.sortino)}</div></div>
                <div className="stat"><div className="stat-label">Calmar</div><div className="stat-value mono">{fmtNum(tearsheet.quantstats.calmar)}</div></div>
                <div className={`stat ${numClass(tearsheet.quantstats.max_drawdown_pct)}`}><div className="stat-label">最大回撤</div><div className="stat-value mono">{fmtPct(tearsheet.quantstats.max_drawdown_pct)}</div></div>
                <div className="stat"><div className="stat-label">日胜率</div><div className="stat-value mono">{fmtPct(tearsheet.quantstats.win_rate_pct, 2, false)}</div></div>
                <div className="stat"><div className="stat-label">VaR(95%)</div><div className="stat-value mono">{fmtPct(tearsheet.quantstats.var_95_pct)}</div></div>
              </div>
              {tearsheet.quantstats.worst_dd_window && (
                <p className="muted small">
                  最深回撤窗口：{tearsheet.quantstats.worst_dd_window.start} → {tearsheet.quantstats.worst_dd_window.end}
                  （{tearsheet.quantstats.worst_dd_window.days} 天，{fmtPct(tearsheet.quantstats.worst_dd_window.depth_pct)}）
                </p>
              )}
              {tearsheet.report.ok
                ? <p className="muted small">完整 HTML 报告已写入 Obsidian：<span className="mono">{tearsheet.report.obsidian_relpath}</span>（{tearsheet.report.size_kb} KB）</p>
                : <p className="muted small">HTML 报告未生成：{tearsheet.report.error}</p>}
            </>
          ) : (
            <ErrorBanner message={tearsheet.quantstats.error ?? '绩效计算失败'} />
          )}
        </Card>
      )}

      {resp && resp.ok === false && (
        <ErrorBanner message={`回测未完成：${resp.error ?? '未知原因（可能历史数据不足）'}`} />
      )}

      {resp?.ok && m && (
        <>
          <div className="stat-row stats-wrap">
            <div className={`stat ${numClass(m.total_return_pct)}`}>
              <div className="stat-label">策略总收益</div>
              <div className="stat-value mono">{fmtPct(m.total_return_pct)}</div>
            </div>
            <div className={`stat ${numClass(m.bench_return_pct)}`}>
              <div className="stat-label">基准收益</div>
              <div className="stat-value mono">{fmtPct(m.bench_return_pct)}</div>
            </div>
            <div className={`stat ${numClass(m.cagr_pct)}`}>
              <div className="stat-label">年化 CAGR</div>
              <div className="stat-value mono">{fmtPct(m.cagr_pct)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">夏普比率</div>
              <div className="stat-value mono">{fmtNum(m.sharpe)}</div>
            </div>
            <div className={`stat ${numClass(m.max_drawdown_pct)}`}>
              <div className="stat-label">最大回撤</div>
              <div className="stat-value mono">{fmtPct(m.max_drawdown_pct)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">胜率</div>
              <div className="stat-value mono">{fmtPct(m.win_rate_pct, 2, false)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">交易次数</div>
              <div className="stat-value mono">{fmtInt(m.trades)}</div>
            </div>
          </div>

          {lineOpt && (
            <Card title="净值曲线 vs 基准">
              <ReactECharts option={lineOpt} notMerge style={{ height: 340 }} opts={{ renderer: 'canvas' }} />
            </Card>
          )}

          <Card title={`交易清单 (${trades.length})`}>
            {trades.length === 0 ? (
              <EmptyState>回测区间内没有产生交易</EmptyState>
            ) : (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>入场 Entry</th>
                      <th>离场 Exit</th>
                      <th className="num">盈亏 PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i}>
                        <td className="muted mono">{i + 1}</td>
                        <td className="mono">{tradeCell(t.entry)}</td>
                        <td className="mono">{tradeCell(t.exit)}</td>
                        <td className={`num mono ${numClass(t.pnl)}`}>{fmtNum(t.pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}

/** Render entry/exit cells which may be scalars or structured objects. */
function tradeCell(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'number' || typeof v === 'string') return String(v);
  if (typeof v === 'object') {
    const rec = v as Record<string, unknown>;
    const date = rec.date ?? rec.datetime ?? rec.day;
    const price = rec.price ?? rec.close ?? rec.px;
    const parts: string[] = [];
    if (date != null) parts.push(String(date));
    if (price != null) parts.push(String(price));
    if (parts.length > 0) return parts.join(' @ ');
    try {
      return JSON.stringify(rec);
    } catch {
      return '—';
    }
  }
  return String(v);
}
