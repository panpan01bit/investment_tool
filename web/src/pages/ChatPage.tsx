import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { api, type ChatSource } from '../lib/api';
import { errText, truncate } from '../lib/format';
import { Card, PageHead } from '../components/ui';

interface Msg {
  role: 'user' | 'assistant';
  text: string;
  sources?: ChatSource[];
  error?: boolean;
}

const WELCOME =
  '我是观澜研究助理，可以基于本地简报、持仓、信号与报告库回答投研问题。勾选「联网搜索」可补充实时网络证据。试试下面的示例问题。';

const SAMPLES = [
  '最近一期晨报的宏观要点是什么？',
  '当前组合里哪个标的信号最强？',
  'A 类卖铲子赛道有哪些代表公司？',
];

export default function ChatPage() {
  const [msgs, setMsgs] = useState<Msg[]>([{ role: 'assistant', text: WELCOME }]);
  const [input, setInput] = useState('');
  const [useSearch, setUseSearch] = useState(false);
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [msgs]);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput('');
    setMsgs((prev) => [...prev, { role: 'user', text: q }, { role: 'assistant', text: '' }]);
    setBusy(true);
    try {
      const r = await api.chat(q, useSearch);
      patchLast({ text: r.answer ?? '', sources: r.sources ?? [] });
    } catch (e) {
      patchLast({ text: `请求失败：${errText(e)}`, error: true });
    } finally {
      setBusy(false);
    }
  };

  const patchLast = (patch: Partial<Msg>) => {
    setMsgs((prev) => {
      if (prev.length === 0) return prev;
      const cp = [...prev];
      cp[cp.length - 1] = { ...cp[cp.length - 1], ...patch };
      return cp;
    });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="page page-chat">
      <PageHead title="研究问答" subtitle="基于本地投研语料的对话式检索与推理" />

      <Card className="chat-card">
        <div className="chat-list" ref={listRef}>
          {msgs.map((m, i) => (
            <div key={i} className={`chat-row ${m.role === 'user' ? 'from-user' : 'from-bot'}`}>
              <div className={`bubble ${m.role}${m.error ? ' bubble-error' : ''}`}>
                {m.text ? m.text : <span className="typing muted">思考中<span className="dots" /></span>}
                {m.role === 'assistant' && m.sources && m.sources.length > 0 && (
                  <ol className="sources">
                    {m.sources.map((s, j) => (
                      <li key={j}>
                        {s.url ? (
                          <a href={s.url} target="_blank" rel="noopener noreferrer">
                            <sup className="mono">[{j + 1}]</sup> {truncate(s.title || s.url, 60)}
                            {s.domain && <span className="muted"> · {s.domain}</span>}
                          </a>
                        ) : (
                          <span>
                            <sup className="mono">[{j + 1}]</sup> {truncate(s.title, 60)}
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </div>
          ))}
          {msgs.length <= 1 && (
            <div className="samples">
              {SAMPLES.map((s) => (
                <button
                  key={s}
                  className="chip sample-chip"
                  onClick={() => {
                    setInput(s);
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="chat-input-row">
          <label className="search-toggle">
            <input type="checkbox" checked={useSearch} onChange={(e) => setUseSearch(e.target.checked)} />
            联网搜索
            <span className="muted small">{useSearch ? '(Tavily)' : ''}</span>
          </label>
          <textarea
            className="input chat-textarea"
            rows={2}
            placeholder="输入问题…（Enter 发送，Shift+Enter 换行）"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="btn btn-primary send-btn" onClick={() => void send()} disabled={busy || !input.trim()}>
            发送
          </button>
        </div>
      </Card>
    </div>
  );
}
