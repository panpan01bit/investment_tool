import type { PortfolioRowInput } from './api';

/** Format a percentage value with sign; null-safe. */
export function fmtPct(v: number | null | undefined, digits = 2, withSign = true): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v).toFixed(digits);
  const sign = withSign && v > 0 ? '+' : '';
  return `${sign}${abs}%`;
}

/** Format a plain number with fixed digits + thousand separators. */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Format an integer count. */
export function fmtInt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

/** Money with CJK-friendly currency symbol based on position currency. */
export function fmtMoney(v: number | null | undefined, currency?: string | null): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const cur = (currency ?? '').toUpperCase();
  let sym = '¥';
  if (cur === 'USD') sym = '$';
  else if (cur === 'HKD') sym = 'HK$';
  const n = v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${sym}${n}`;
}

/** CSS class helper for China convention coloring: up=red, down=green. */
export function numClass(v: number | null | undefined): '' | 'up' | 'down' {
  if (v == null || !Number.isFinite(v) || v === 0) return '';
  return v > 0 ? 'up' : 'down';
}

/** Extract a readable message out of any thrown value. */
export function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === 'string') return e;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

/** Run async work over items with bounded concurrency, preserving order. */
export async function pooledMap<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;
  const workerCount = Math.max(1, Math.min(limit, items.length));
  const workers: Promise<void>[] = [];
  for (let w = 0; w < workerCount; w++) {
    workers.push(
      (async () => {
        for (;;) {
          const i = cursor++;
          if (i >= items.length) return;
          results[i] = await fn(items[i], i);
        }
      })(),
    );
  }
  await Promise.all(workers);
  return results;
}

export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

/** Normalize arrays-or-string fields from backend into string[] lines. */
export function linesOf(v: unknown): string[] {
  if (v == null) return [];
  if (Array.isArray(v)) {
    return v.map((x) => (typeof x === 'string' ? x : typeof x === 'number' ? String(x) : ''));
  }
  if (typeof v === 'string') return v.split(/\n+/).filter(Boolean);
  return [];
}

/* ---------------- CSV parsing (portfolio import) ---------------- */

/** Split one CSV line honoring double-quoted cells; accepts comma or tab separators. */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',' || ch === '\t') {
      out.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur.trim());
  return out;
}

/**
 * Parse pasted/uploaded portfolio CSV text into API row inputs.
 * Expected columns: symbol,name,quantity,cost_price
 * Returns parsed rows plus raw lines that were skipped.
 */
export function parsePortfolioCsv(text: string): { rows: PortfolioRowInput[]; skipped: string[] } {
  const rows: PortfolioRowInput[] = [];
  const skipped: string[] = [];
  const lines = text.split(/\r?\n/);
  lines.forEach((rawLine, idx) => {
    const line = rawLine.trim();
    if (!line) return;
    const cells = splitCsvLine(line);
    // Skip header row when detected
    if (idx === 0 && /^(symbol|代码|标的)$/i.test(cells[0] ?? '')) return;
    if (cells.length < 3) {
      skipped.push(truncate(line, 80));
      return;
    }
    const qty = Number(cells[2].replace(/,/g, ''));
    const costRaw = cells.length >= 4 ? Number(cells[3].replace(/,/g, '')) : NaN;
    if (!cells[0] || !Number.isFinite(qty)) {
      skipped.push(truncate(line, 80));
      return;
    }
    rows.push({
      symbol: cells[0],
      name: cells[1] && !Number.isFinite(Number(cells[1])) ? cells[1] : cells[0],
      quantity: qty,
      cost_price: Number.isFinite(costRaw) ? costRaw : 0,
    });
  });
  return { rows, skipped };
}
