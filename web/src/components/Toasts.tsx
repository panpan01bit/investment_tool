import { useEffect, useState } from 'react';
import { dismissToast, subscribeToasts } from '../lib/toast';
import type { ToastItem } from '../lib/toast';

export default function Toasts() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => subscribeToasts(setItems), []);
  return (
    <div className="toasts">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => dismissToast(t.id)}>
          <span className="toast-dot" />
          <span className="toast-text">{t.text}</span>
        </div>
      ))}
    </div>
  );
}
