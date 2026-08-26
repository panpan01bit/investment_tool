import { useEffect, type ReactNode } from 'react';
import type { SignalRule } from '../lib/api';
import { pushToast } from '../lib/toast';
import { DirectionCell } from './ui-cells';

/* ------------------------------ containers ------------------------------ */

export function Card({
  title,
  extra,
  children,
  className,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card${className ? ` ${className}` : ''}`}>
      {(title != null || extra != null) && (
        <header className="card-head">
          {title != null && <h2 className="card-title">{title}</h2>}
          {extra != null && <div className="card-extra">{extra}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHead({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  width = 560,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  width?: number | string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" style={{ width }}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} title="关闭" aria-label="关闭">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function Drawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      <div className="overlay-under" onMouseDown={onClose} />
      <aside className="drawer">
        <div className="drawer-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} title="关闭" aria-label="关闭">
            ×
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

/* ------------------------------ feedback ------------------------------ */

export function Spinner({ size = 16 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden />;
}

export function LoadingBlock({ label = '加载中…' }: { label?: string }) {
  return (
    <div className="loading-block">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorBanner({
  message,
  onClose,
  onRetry,
}: {
  message: ReactNode;
  onClose?: () => void;
  onRetry?: () => void;
}) {
  return (
    <div className="banner banner-error">
      <span className="banner-ico">!</span>
      <div className="banner-msg">{message}</div>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          重试
        </button>
      )}
      {onClose && (
        <button className="banner-x" onClick={onClose} aria-label="关闭">
          ×
        </button>
      )}
    </div>
  );
}

export function InfoBanner({ children }: { children: ReactNode }) {
  return <div className="banner banner-info">{children}</div>;
}

export function WarnBanner({ children }: { children: ReactNode }) {
  return (
    <div className="banner banner-warn">
      <span className="banner-ico">!</span>
      <div className="banner-msg">{children}</div>
    </div>
  );
}

export function StatCard({
  label,
  value,
  sub,
  tone = '',
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: string;
}) {
  return (
    <div className={`stat${tone ? ` ${tone}` : ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub != null && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/* ------------------------------ atoms ------------------------------ */

/** Score chip with intensity gradient: 红涨绿跌 China convention. */
export function ScoreChip({ score, stance }: { score?: number | null; stance?: string }) {
  if (score == null || !Number.isFinite(score)) {
    return <span className="chip chip-muted">无信号</span>;
  }
  const s = Math.max(-100, Math.min(100, score));
  const mag = Math.abs(s);
  const rgb = s >= 0 ? '239, 68, 68' : '34, 197, 94';
  const alpha = 0.14 + (mag / 100) * 0.52;
  const text = s >= 0 ? '#fca5a5' : '#86efac';
  return (
    <span
      className="chip score-chip"
      title={`得分 ${s} / 100`}
      style={{
        background: `rgba(${rgb}, ${alpha.toFixed(2)})`,
        borderColor: `rgba(${rgb}, ${Math.min(1, alpha + 0.3).toFixed(2)})`,
        color: text,
      }}
    >
      <strong className="mono">{s > 0 ? `+${s}` : String(s)}</strong>
      {stance ? <em>{stance}</em> : null}
    </span>
  );
}

/**
 * Horizontal score bar (-100..100), red on the right (bullish), green on the left.
 * Returns null when no valid score.
 */
export function ScoreBar({ score }: { score: number | null | undefined }) {
  if (score == null || !Number.isFinite(score)) return <span className="muted mono">—</span>;
  const s = Math.max(-100, Math.min(100, score));
  const pos = (s + 100) / 2; // 0..100 along the bar
  const color = s >= 0 ? '#ef4444' : '#22c55e';
  return (
    <div className="scorebar-wrap" title={`${s}`}>
      <div className="scorebar">
        <div className="scorebar-zero" />
        <div
          className="scorebar-fill"
          style={
            s >= 0
              ? { left: '50%', width: `${pos - 50}%`, background: color }
              : { left: `${pos}%`, width: `${50 - pos}%`, background: color }
          }
        />
      </div>
      <span className={`mono scorebar-num`}>{s > 0 ? `+${s}` : s}</span>
    </div>
  );
}

export { DirectionCell };

export function RulesList({ rules }: { rules?: SignalRule[] | null }) {
  if (!rules || rules.length === 0) return <EmptyState>暂无规则明细</EmptyState>;
  return (
    <ul className="rules-list">
      {rules.map((r, i) => (
        <li key={`${r.name}-${i}`} className="rule-row">
          <DirectionCell direction={r.direction} />
          <div className="rule-main">
            <div className="rule-name">
              {r.name}
              {r.weight != null && Number.isFinite(Number(r.weight)) && (
                <span className="rule-weight mono">×{r.weight}</span>
              )}
            </div>
            {r.reason && <div className="rule-reason muted">{r.reason}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function ConfidenceBadge({ confidence }: { confidence?: number | null }) {
  if (confidence == null || !Number.isFinite(confidence)) return null;
  // Accept both 0..1 and 0..100 encodings.
  const c = confidence <= 1 ? confidence * 100 : confidence;
  const tone = c >= 70 ? 'high' : c >= 40 ? 'mid' : 'low';
  return <span className={`conf conf-${tone}`}>置信度 {c.toFixed(0)}%</span>;
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button
          key={t.key}
          className={`tab${active === t.key ? ' active' : ''}`}
          onClick={() => onChange(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function CopyBtn({ text, label = '复制路径' }: { text?: string | null; label?: string }) {
  const copy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      pushToast('success', '已复制到剪贴板');
    } catch {
      pushToast('error', '复制失败，请手动选择文本');
    }
  };
  return (
    <button className="btn btn-sm btn-ghost" onClick={copy} disabled={!text}>
      {label}
    </button>
  );
}

export function NotePath({ path }: { path?: string | null }) {
  if (!path) return null;
  return (
    <div className="notepath">
      <span className="mono notepath-text">{path}</span>
      <CopyBtn text={path} />
    </div>
  );
}
