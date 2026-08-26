// Tiny global toast store: publish/subscribe so both UI and the API client
// (plain TS modules, outside React) can push notifications.

export type ToastKind = 'info' | 'success' | 'error';

export interface ToastItem {
  id: number;
  kind: ToastKind;
  text: string;
}

let seq = 0;
let items: ToastItem[] = [];
const subs = new Set<(items: ToastItem[]) => void>();

function emit(): void {
  const snapshot = items.slice();
  subs.forEach((fn) => fn(snapshot));
}

export function pushToast(kind: ToastKind, text: string, ttlMs = 5000): void {
  const id = ++seq;
  items = [...items.slice(-4), { id, kind, text }];
  emit();
  window.setTimeout(() => dismissToast(id), ttlMs);
}

export function dismissToast(id: number): void {
  items = items.filter((t) => t.id !== id);
  emit();
}

export function subscribeToasts(fn: (items: ToastItem[]) => void): () => void {
  subs.add(fn);
  fn(items);
  return () => {
    subs.delete(fn);
  };
}
