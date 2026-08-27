import { useEffect, useMemo, useState } from 'react';
import {
  api,
  type OptimizeResp,
  type PortfolioResp,
  type PortfolioRowInput,
  type Signal,
} from '../lib/api';
import { errText, fmtMoney, fmtNum, fmtPct, numClass, parsePortfolioCsv } from '../lib/format';
import { pushToast } from '../lib/toast';
import { pooledMap } from '../lib/format';
import { Icon } from '../components/icons';
import {
  Card,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  Modal,
  PageHead,
  ScoreChip,
  Spinner,
} from '../components/ui';

export default function PortfolioPage() {
  const [data, setData] = useState<PortfolioResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  // Lazy per-symbol signal chips
  const [sigMap, setSigMap] = useState<Record<string, Signal | null>>({});
  const [sigLoading, setSigLoading] = useState(false);

  // CSV import modal
  const [csvOpen, setCsvOpen] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [saving, setSaving] = useState(false);

  // 组合优化（PyPortfolioOpt）
  const [optOpen, setOptOpen] = useState(false);
  const [optMethod, setOptMethod] = useState('hrp');
  const [opt, setOpt] = useState<OptimizeResp | null>(null);
  const [optLoading, setOptLoading] = useState(false);

  const runOptimize = async () => {
    setOptLoading(true);
    try {
      const r = await api.portfolioOptimize(optMethod);
      setOpt(r);
      pushToast('success', `优化完成（${r.method}）：${Object.keys(r.weights ?? {}).length} 只标的`);
    } catch (e) {
      pushToast('error', `优化失败：${errText(e)}`, 8000);
    } finally {
      setOptLoading(false);
    }
  };

  const load = async () => {
    setLoading(true);
    setErr('');
    try {
      setData(await api.portfolio());
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

  // Batch-fetch signals with concurrency 4 once positions are loaded.
  useEffect(() => {
    if (!data || data.positions.length === 0) return;
    let cancelled = false;
    setSigLoading(true);
    setSigMap({});
    const symbols = Array.from(new Set(data.positions.map((p) => p.symbol)));
    void (async () => {
      await pooledMap(symbols, 4, async (sym) => {
        try {
          const s = await api.signals(sym);
          if (!cancelled) setSigMap((prev) => ({ ...prev, [sym]: s }));
        } catch {
          if (!cancelled) setSigMap((prev) => ({ ...prev, [sym]: null }));
        }
      });
      if (!cancelled) setSigLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [data]);

  const sorted = useMemo(() => {
    if (!data) return [];
    return [...data.positions].sort(
      (a, b) => (b.weight_pct ?? 0) - (a.weight_pct ?? 0),
    );
  }, [data]);

  const parsedCsv = useMemo(() => parsePortfolioCsv(csvText), [csvText]);

  const handleFile = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setCsvText(String(reader.result ?? ''));
    reader.onerror = () => pushToast('error', `读取文件失败：${file.name}`);
    reader.readAsText(file);
  };

  const saveRows = async () => {
    if (parsedCsv.rows.length === 0) return;
    setSaving(true);
    try {
      const r = await api.savePortfolio(parsedCsv.rows);
      pushToast('success', `已保存 ${parsedCsv.rows.length} 条持仓 → ${r.saved_to}`, 6000);
      setCsvOpen(false);
      setCsvText('');
      await load();
    } catch (e) {
      pushToast('error', `保存失败：${errText(e)}`, 8000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page">
      <PageHead
        title="持仓组合"
        subtitle="组合权重 · 盈亏与单标的量化信号速览"
        actions={
          <>
            <button className="btn" onClick={() => void load()}>
              <Icon name="refresh" size={14} />
              刷新
            </button>
            <button className="btn" onClick={() => setOptOpen(!optOpen)}>
              优化建议
            </button>
            <button className="btn btn-primary" onClick={() => setCsvOpen(true)}>
              <Icon name="upload" size={14} />
              上传CSV
            </button>
          </>
        }
      />

      {err && <ErrorBanner message={err} onRetry={() => void load()} />}
      {!err && loading && !data && <LoadingBlock label="加载持仓…" />}

      {optOpen && (
        <Card title="组合优化建议（PyPortfolioOpt）">
          <div className="bt-controls">
            <select
              className="input"
              value={optMethod}
              onChange={(e) => setOptMethod(e.target.value)}
              disabled={optLoading}
            >
              <option value="hrp">层次风险平价 HRP（稳健，无需收益估计）</option>
              <option value="max_sharpe">最大夏普（历史均值估计）</option>
              <option value="min_volatility">最小波动</option>
            </select>
            <button className="btn btn-primary" onClick={() => void runOptimize()} disabled={optLoading}>
              {optLoading && <Spinner size={14} />}
              {optLoading ? '优化中…' : '运行优化'}
            </button>
            <span className="muted small">
              至少2只持仓 · 单标的≤35% · 基于近一年历史协方差，非投资建议
            </span>
          </div>
          {opt?.ok && (
            <div className="tbl-wrap" style={{ marginTop: 12 }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>标的</th>
                    <th className="num">当前权重</th>
                    <th className="num">目标权重</th>
                    <th className="num">偏离</th>
                    <th>动作</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(opt.weights ?? {}).map(([sym, w]) => {
                    const s = (opt.suggestions ?? []).find((x) => x.symbol === sym);
                    const cur = opt.current_weights?.[sym] ?? 0;
                    const tgt = w * 100;
                    const diff = tgt - cur;
                    return (
                      <tr key={sym}>
                        <td className="mono">{sym}</td>
                        <td className="num mono">{cur.toFixed(1)}%</td>
                        <td className="num mono"><b>{tgt.toFixed(1)}%</b></td>
                        <td className={`num mono ${Math.abs(diff) >= 3 ? (diff > 0 ? 'up-ok' : 'down-warn') : 'muted'}`}>
                          {diff >= 0 ? '+' : ''}{diff.toFixed(1)}%
                        </td>
                        <td>{s?.action ?? (Math.abs(diff) >= 3 ? (diff > 0 ? '买入增持' : '减持') : '持有')}</td>
                      </tr>
                    );
                  })}
                  {(opt.suggestions ?? [])
                    .filter((x) => !(opt.weights ?? {})[x.symbol])
                    .map((x) => (
                      <tr key={x.symbol}>
                        <td className="mono">{x.symbol}</td>
                        <td className="num mono">{x.current_pct.toFixed(1)}%</td>
                        <td className="num mono"><b>0.0%</b></td>
                        <td className="num mono down-warn">{x.diff_pct.toFixed(1)}%</td>
                        <td>{x.action}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
              <p className="muted small">
                {(opt.metrics as { method_note?: string })?.method_note ?? ''} · {opt.disclaimer}
              </p>
            </div>
          )}
        </Card>
      )}

      {data && (
        <>
          <div className="stat-row">
            <div className="stat">
              <div className="stat-label">总市值（折合人民币）</div>
              <div className="stat-value mono">{fmtMoney(data.total_value_cny)}</div>
            </div>
            <div className={`stat ${numClass(data.total_pnl_cny)}`}>
              <div className="stat-label">总盈亏</div>
              <div className="stat-value mono">{fmtMoney(data.total_pnl_cny)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">持仓数量</div>
              <div className="stat-value mono">{data.positions.length}</div>
            </div>
            <div className="stat">
              <div className="stat-label">信号状态</div>
              <div className="stat-value">{sigLoading ? '计算中…' : '已就绪'}</div>
            </div>
          </div>

          {data.skipped && data.skipped.length > 0 && (
            <div className="banner banner-warn">
              <span className="banner-ico">!</span>
              <div className="banner-msg" title={data.skipped.join('\n')}>
                有 {data.skipped.length} 行无法解析被跳过：{data.skipped.slice(0, 3).join('；')}
                {data.skipped.length > 3 ? ' …' : ''}
              </div>
            </div>
          )}

          <Card title="明细 · 按权重排序">
            {sorted.length === 0 ? (
              <EmptyState>暂无持仓记录，点击右上角「上传CSV」导入第一笔仓位。</EmptyState>
            ) : (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>类别</th>
                      <th>标的 / 名称</th>
                      <th className="num">数量</th>
                      <th className="num">成本价</th>
                      <th className="num">现价</th>
                      <th className="num">涨跌幅</th>
                      <th className="num">市值</th>
                      <th className="num">盈亏 / 幅度</th>
                      <th>权重</th>
                      <th>量化信号</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((p) => {
                      const sig = sigMap[p.symbol];
                      return (
                        <tr key={`${p.symbol}-${p.category ?? ''}`}>
                          <td>
                            <span className="chip chip-muted">{p.category || '—'}</span>
                          </td>
                          <td>
                            <div className="pos-sym">
                              <span className="mono">{p.symbol}</span>
                              <span className="muted">{p.name || p.symbol}</span>
                            </div>
                          </td>
                          <td className="num mono">{fmtNum(p.quantity, 0)}</td>
                          <td className="num mono">{fmtNum(p.cost_price)}</td>
                          <td className="num mono">
                            {fmtNum(p.price)}
                            <span className="quote-src muted" title={`报价来源：${p.quote_source ?? '-'}`}>
                              {p.quote_source}
                            </span>
                          </td>
                          <td className={`num mono ${numClass(p.change_pct)}`}>{fmtPct(p.change_pct)}</td>
                          <td className="num mono">{fmtMoney(p.market_value, p.currency)}</td>
                          <td className="num">
                            <div className={`mono ${numClass(p.pnl)}`}>{fmtMoney(p.pnl, p.currency)}</div>
                            <div className={`mono small ${numClass(p.pnl_pct)}`}>{fmtPct(p.pnl_pct)}</div>
                          </td>
                          <td>
                            <div className="weight-cell">
                              <div className="weight-bar">
                                <span style={{ width: `${Math.min(100, Math.max(0, p.weight_pct ?? 0))}%` }} />
                              </div>
                              <span className="mono small muted">{fmtNum(p.weight_pct, 1)}%</span>
                            </div>
                          </td>
                          <td>
                            {sig === undefined ? (
                              <span className="muted mono small">{sigLoading ? '…' : '—'}</span>
                            ) : sig ? (
                              <ScoreChip score={sig.score} stance={sig.stance} />
                            ) : (
                              <span className="chip chip-muted">无数据</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                  <tfoot>
                    <tr className="totals-row">
                      <td colSpan={6}>合计 TOTAL</td>
                      <td className="num mono">{fmtMoney(data.total_value_cny)}</td>
                      <td className={`num mono ${numClass(data.total_pnl_cny)}`}>
                        {fmtMoney(data.total_pnl_cny)}
                      </td>
                      <td colSpan={2} />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {/* CSV import modal */}
      <Modal open={csvOpen} title="上传持仓 CSV" onClose={() => setCsvOpen(false)} width={640}>
        <p className="modal-hint">
          支持逗号或制表符分隔，前四列依次为 <code>symbol</code>, <code>name</code>,
          <code>quantity</code>, <code>cost_price</code>。可粘贴文本或选择文件，首行表头会被自动忽略。
        </p>
        <pre className="code-sample">
{`symbol,name,quantity,cost_price
300308,中际旭创,200,95.50
00700.HK,腾讯控股,100,382.00
NVDA,英伟达,10,118.20`}
        </pre>
        <label className="file-row">
          <input
            type="file"
            accept=".csv,.txt,text/csv,text/plain"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <span className="muted small">选择 .csv 文件（也可直接在下方粘贴）</span>
        </label>
        <textarea
          className="input csv-area"
          rows={7}
          placeholder={'300308,中际旭创,200,95.50\n00700.HK,腾讯控股,100,382.00'}
          value={csvText}
          onChange={(e) => setCsvText(e.target.value)}
        />
        <div className="modal-foot">
          <span className="muted small">
            解析结果：
            <b className="up-ok">{parsedCsv.rows.length}</b> 行有效
            {parsedCsv.skipped.length > 0 && (
              <>
                {' · '}
                <b className="down-warn">{parsedCsv.skipped.length}</b> 行无效将被跳过
              </>
            )}
          </span>
          <div className="modal-foot-actions">
            <button className="btn" onClick={() => setCsvText('')} disabled={!csvText}>
              清空
            </button>
            <button
              className="btn btn-primary"
              onClick={() => void saveRows()}
              disabled={parsedCsv.rows.length === 0 || saving}
            >
              {saving && <Spinner size={14} />}
              导入 {parsedCsv.rows.length} 行
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
