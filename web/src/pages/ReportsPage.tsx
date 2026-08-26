import { useEffect, useRef, useState } from 'react';
import {
  api,
  type FigureMeta,
  type ReportRecord,
  type ReportSummary,
} from '../lib/api';
import { errText, fmtInt, fmtNum, linesOf } from '../lib/format';
import { pushToast } from '../lib/toast';
import { Icon } from '../components/icons';
import { Card, Drawer, EmptyState, ErrorBanner, LoadingBlock, PageHead, Spinner, NotePath } from '../components/ui';

export default function ReportsPage() {
  const [reports, setReports] = useState<ReportRecord[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [parsingId, setParsingId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    setLoading(true);
    setErr('');
    try {
      const r = await api.reports();
      setReports(r.reports ?? []);
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

  const uploadFiles = async (files: FileList | File[] | null) => {
    if (!files) return;
    const list = Array.from(files).filter((f) => /\.pdf$/i.test(f.name));
    const rejected = Array.from(files).length - list.length;
    if (rejected > 0) pushToast('error', `已跳过 ${rejected} 个非 PDF 文件（仅支持 PDF）`);
    if (list.length === 0) return;
    setUploading(true);
    for (const f of list) {
      try {
        const r = await api.uploadReport(f);
        pushToast('success', `已上传：${f.name}（${fmtInt(r.record?.n_pages ?? 0)} 页）`);
        if (r.next) pushToast('info', String(r.next), 9000);
      } catch (e) {
        pushToast('error', `上传失败 ${f.name}：${errText(e)}`, 8000);
      }
    }
    setUploading(false);
    await load();
  };

  const parse = async (rid: string) => {
    setParsingId(rid);
    try {
      const r = await api.parseReport(rid);
      setReports((prev) =>
        (prev ?? []).map((x) =>
          x.id === rid
            ? {
                ...x,
                status: r.record?.status ?? 'parsed',
                summary: r.record?.summary ?? x.summary,
                figures_meta: r.record?.figures_meta ?? x.figures_meta,
                vision_done: r.record?.vision_done ?? true,
                obsidian_note: r.obsidian_note ?? x.obsidian_note ?? null,
              }
            : x,
        ),
      );
      pushToast('success', '解析完成，结构化摘要与图表抽取已入库');
      const notePath = r.obsidian_note ?? r.record?.obsidian_note;
      if (notePath) pushToast('info', `笔记位置：${notePath}`, 9000);
    } catch (e) {
      pushToast('error', `解析失败：${errText(e)}`, 9000);
    } finally {
      setParsingId(null);
    }
  };

  const openRec = reports?.find((r) => r.id === openId) ?? null;

  return (
    <div className="page">
      <PageHead
        title="报告库"
        subtitle="券商研报 PDF 上传 · 视觉图表抽取 · 结构化摘要"
        actions={
          <button className="btn" onClick={() => void load()}>
            <Icon name="refresh" size={14} />
            刷新
          </button>
        }
      />

      {/* upload dropzone */}
      <div
        className={`dropzone${dragOver ? ' drag' : ''}${uploading ? ' busy' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void uploadFiles(e.dataTransfer.files);
        }}
        onClick={() => fileRef.current?.click()}
      >
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          onChange={(e) => void uploadFiles(e.target.files)}
        />
        <Icon name="upload" size={22} />
        <div>
          {uploading ? (
            <span className="dropzone-title">
              <Spinner size={14} /> 正在上传，请稍候…
            </span>
          ) : (
            <>
              <span className="dropzone-title">拖拽 PDF 研报到此处，或点击选择文件</span>
              <span className="muted small">上传后可在列表中点击「解析」进行视觉图表抽取（耗时约数分钟）</span>
            </>
          )}
        </div>
      </div>

      {err && <ErrorBanner message={err} onRetry={() => void load()} />}
      {!reports && loading && !err && <LoadingBlock label="加载报告清单…" />}

      {reports && reports.length === 0 && !err && (
        <EmptyState>报告库为空 — 先拖入一份 PDF 研报试试。</EmptyState>
      )}

      {reports && reports.length > 0 && (
        <Card title={`报告清单 (${reports.length})`}>
          <div className="tbl-wrap">
            <table className="tbl clickable">
              <thead>
                <tr>
                  <th>标题</th>
                  <th>券商</th>
                  <th>日期</th>
                  <th>评级</th>
                  <th className="num">目标价</th>
                  <th>标的</th>
                  <th className="num">页数</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => (
                  <tr key={r.id} onClick={() => setOpenId(r.id)} title="点击查看详情">
                    <td className="rep-title-cell">
                      <span className="rep-title">{r.title || r.filename}</span>
                      <span className="muted small">{r.filename}</span>
                    </td>
                    <td>{r.broker || '—'}</td>
                    <td className="mono">{r.date || '—'}</td>
                    <td>
                      <span className={`chip rating-${ratingTone(r.rating)}`}>{r.rating || '—'}</span>
                    </td>
                    <td className="num mono">{fmtNum(r.target_price ?? undefined)}</td>
                    <td>
                      {(r.symbols ?? []).length > 0 ? (
                        <span title={(r.symbols ?? []).map((s) => `${s.symbol} ${s.name ?? ''}`).join('、')}>
                          {(r.symbols ?? []).slice(0, 2).map((s) => s.symbol).join('、')}
                          {(r.symbols ?? []).length > 2 ? ` 等${r.symbols!.length}` : ''}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="num mono">{r.n_pages ?? '—'}</td>
                    <td>
                      <StatusBadge rec={r} />
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="row-actions">
                        {r.status !== 'parsed' && (
                          <button
                            className="btn btn-sm btn-primary"
                            onClick={() => void parse(r.id)}
                            disabled={parsingId !== null}
                            title="视觉解析（可能需要几分钟）"
                          >
                            {parsingId === r.id ? <Spinner size={12} /> : null}
                            {parsingId === r.id ? '解析中…' : '解析'}
                          </button>
                        )}
                        <a
                          className="btn btn-sm btn-ghost"
                          href={api.reportPdfUrl(r.id)}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          原始PDF
                        </a>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {parsingId && (
            <p className="muted small parse-hint">
              <Spinner size={12} /> 正在解析报告（视觉图表抽取较慢，约需数分钟），完成后将自动刷新该行。
            </p>
          )}
        </Card>
      )}

      <Drawer open={!!openRec} onClose={() => setOpenId(null)} title="研报详情">
        {openRec ? <ReportDetail rec={openRec} /> : null}
      </Drawer>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function ratingTone(rating?: string | null): string {
  if (!rating) return '';
  if (/买入|强烈推荐|强推/.test(rating)) return 'up';
  if (/增持|推荐|优于/.test(rating)) return 'warm';
  if (/减持|卖出|回避/.test(rating)) return 'down';
  return '';
}

function StatusBadge({ rec }: { rec: ReportRecord }) {
  const cls = /^parsed/i.test(rec.status) ? 'chip-ok' : /pending|processing|running/i.test(rec.status) ? 'chip-warm' : '';
  return (
    <span className={`chip ${cls}`}>
      {rec.status}
      {rec.vision_done ? ' · 已视觉' : ''}
    </span>
  );
}

function ReportDetail({ rec }: { rec: ReportRecord }) {
  const s: ReportSummary | undefined = rec.summary ?? undefined;
  return (
    <div className="report-detail">
      <div className="detail-meta">
        <span className="mono muted">{rec.id}</span>
        {rec.broker && <span className="chip chip-muted">{rec.broker}</span>}
        {rec.date && <span className="muted mono small">{rec.date}</span>}
        {rec.rating && <span className={`chip rating-${ratingTone(rec.rating)}`}>{rec.rating}</span>}
        {rec.target_price != null && (
          <span className="muted mono small">目标价 {fmtNum(rec.target_price)}</span>
        )}
      </div>

      {!s || (!s.one_liner && !(s.core_views ?? []).length) ? (
        <EmptyState>
          尚未解析 — 在列表中点击「解析」生成结构化摘要与图表抽取结果。
        </EmptyState>
      ) : (
        <>
          {s.one_liner && <p className="one-liner">{s.one_liner}</p>}
          <SummaryBlock title="核心观点" items={s.core_views} />
          <SummaryBlock title="投资逻辑" items={s.investment_logic} />
          <SummaryBlock title="风险提示" items={s.risks} tone="risk" />
          <SummaryBlock title="催化剂" items={s.catalysts} />
        </>
      )}

      {(rec.figures_meta ?? []).length > 0 && (
        <section>
          <h4 className="detail-sec">图表抽取结果 ({rec.figures_meta!.length})</h4>
          <div className="fig-grid">
            {(rec.figures_meta ?? []).map((f: FigureMeta, i) => (
              <div className="fig-card" key={`${f.file}-${i}`}>
                <div className="fig-top">
                  <span className="chip chip-muted">{f.classify?.type || 'figure'}</span>
                  {f.classify?.needs_manual_check && <span className="chip chip-warm">需人工复核</span>}
                </div>
                <div className="fig-title">{f.classify?.title || f.file}</div>
                {f.classify?.takeaway && <p className="muted small">{f.classify.takeaway}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {rec.obsidian_note && <NotePath path={rec.obsidian_note} />}

      <div className="detail-foot">
        <a className="btn btn-ghost btn-sm" href={api.reportPdfUrl(rec.id)} target="_blank" rel="noopener noreferrer">
          查看原始 PDF →
        </a>
      </div>
    </div>
  );
}

function SummaryBlock({
  title,
  items,
  tone,
}: {
  title: string;
  items?: string[] | string | null;
  tone?: 'risk';
}) {
  const lines = linesOf(items);
  if (lines.length === 0) return null;
  return (
    <section>
      <h4 className="detail-sec">{title}</h4>
      <ul className={`bullets ${tone === 'risk' ? 'bear' : ''}`}>
        {lines.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
    </section>
  );
}
