import { useEffect, useMemo, useState } from 'react';
import { api, type SettingsStatus, type VaultItem } from '../lib/api';
import { errText } from '../lib/format';
import { pushToast } from '../lib/toast';
import { Icon } from '../components/icons';
import { Card, CopyBtn, ErrorBanner, LoadingBlock, NotePath, PageHead, Spinner } from '../components/ui';

const TOKEN_META: { key: keyof NonNullable<SettingsStatus['tokens']>; label: string; desc: string }[] = [
  { key: 'llm', label: '大模型 LLM', desc: '深度研究与晨报摘要生成' },
  { key: 'vision', label: '视觉模型 Vision', desc: '研报图表视觉抽取' },
  { key: 'tushare', label: '行情数据 Tushare', desc: 'K 线 / 基本面 / 实时报价' },
  { key: 'fred', label: '宏观数据 FRED', desc: '海外宏观序列' },
  { key: 'tavily', label: '联网搜索 Tavily', desc: '新闻检索与问答增强' },
];

const CLI_GUIDE: { cmd: string; desc: string }[] = [
  { cmd: 'investlab init-vault', desc: '初始化 Obsidian 金库目录结构与笔记模板' },
  { cmd: 'investlab doctor', desc: '体检：依赖、Token、数据源连通性自检' },
  { cmd: 'investlab daily', desc: '生成今日晨报（宏观 + 持仓 + 新闻焦点）' },
  { cmd: 'investlab serve', desc: '启动本工作台后端 API（默认 127.0.0.1:8300）' },
  { cmd: 'investlab reports ingest --dir ./pdfs', desc: '批量导入研报 PDF 至报告库' },
];

const ENV_DOCS: { name: string; desc: string; example?: string }[] = [
  { name: 'INVESTLAB_VAULT_PATH', desc: 'Obsidian 金库根目录', example: '~/Obsidian/Guanlan' },
  { name: 'INVESTLAB_DATA_DIR', desc: '结构化数据存放目录', example: './data' },
  { name: 'INVESTLAB_MODE', desc: '运行模式（paper / live）', example: 'paper' },
  { name: 'INVESTLAB_TUSHARE_TOKEN', desc: 'Tushare Pro Token' },
  { name: 'INVESTLAB_FRED_API_KEY', desc: 'FRED 宏观数据 API Key' },
  { name: 'INVESTLAB_TAVILY_API_KEY', desc: 'Tavily 搜索 API Key' },
  { name: 'INVESTLAB_LLM_API_KEY', desc: '大模型 API Key' },
  { name: 'INVESTLAB_LLM_BASE_URL', desc: '大模型 OpenAI 兼容 Base URL', example: 'https://api.deepseek.com/v1' },
  { name: 'INVESTLAB_LLM_MODEL', desc: '模型名称', example: 'deepseek-chat' },
  { name: 'INVESTLAB_VISION_ENABLED', desc: '是否启用研报视觉抽取（true/false）', example: 'false' },
];

export default function SettingsPage() {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    void (async () => {
      try {
        setStatus(await api.settingsStatus());
        setErr('');
      } catch (e) {
        setErr(errText(e));
      }
    })();
  }, []);

  return (
    <div className="page">
      <PageHead title="设置" subtitle="服务状态 · 数据源配置 · 使用指南" />
      {err && <ErrorBanner message={err} onRetry={() => window.location.reload()} />}
      {!status && !err && <LoadingBlock label="读取服务状态…" />}

      {status && (
        <>
          <Card title="服务状态">
            <div className="facts-grid facts-wide">
              <div className="fact">
                <div className="fact-k">版本</div>
                <div className="fact-v mono">{status.version || '—'}</div>
              </div>
              <div className="fact">
                <div className="fact-k">运行模式</div>
                <div className="fact-v">{status.mode || '—'}</div>
              </div>
              <div className="fact fact-wide">
                <div className="fact-k">金库路径 vault_path</div>
                <div className="fact-v mono small notepath-inline">
                  <code>{status.vault_path}</code>
                  <CopyBtn text={status.vault_path} />
                </div>
              </div>
              <div className="fact fact-wide">
                <div className="fact-k">数据目录 data_dir</div>
                <div className="fact-v mono small notepath-inline">
                  <code>{status.data_dir}</code>
                  <CopyBtn text={status.data_dir} />
                </div>
              </div>
            </div>

            <h4 className="detail-sec">数据源 Token</h4>
            <div className="token-grid">
              {TOKEN_META.map((t) => {
                const val = status.tokens?.[t.key];
                const ok = typeof val === 'string' && /(已配置|enabled|ok)/i.test(val);
                return (
                  <div className="token-card" key={t.key} title={t.desc}>
                    <span className={`dot${ok ? ' dot-ok' : ''}`} />
                    <div>
                      <div className="token-name">
                        {t.label}
                        <em className={ok ? 'up-ok-text' : 'muted'}>{typeof val === 'string' ? val : '未配置'}</em>
                      </div>
                      <div className="muted small">{t.desc}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card title="命令行速查">
            <ul className="cmd-list">
              {CLI_GUIDE.map((c) => (
                <li key={c.cmd}>
                  <code className="mono">{c.cmd}</code>
                  <span className="muted">{c.desc}</span>
                  <CopyBtn text={c.cmd} label="复制" />
                </li>
              ))}
            </ul>
            <p className="muted small">子命令以实际安装版本为准，运行 investlab --help 查看完整列表。</p>
          </Card>

          <Card title="环境变量参考">
            <pre className="env-block">
{ENV_DOCS.map((e) => `# ${e.desc}\n${e.name}${e.example ? `=${e.example}` : '=\n'}\n`).join('')}
            </pre>
            <table className="tbl env-tbl">
              <thead>
                <tr>
                  <th>变量名</th>
                  <th>说明</th>
                  <th>示例</th>
                </tr>
              </thead>
              <tbody>
                {ENV_DOCS.map((e) => (
                  <tr key={e.name}>
                    <td className="mono">{e.name}</td>
                    <td>{e.desc}</td>
                    <td className="mono muted">{e.example ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <VaultBrowser />
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Vault tree browser                                                  */
/* ------------------------------------------------------------------ */

interface VNode {
  name: string;
  path: string;
  isDir: boolean;
  children: VNode[];
}

function VaultBrowser() {
  const [items, setItems] = useState<VaultItem[] | null>(null);
  const [root, setRoot] = useState('');
  const [loading, setLoading] = useState(false);

  const tree = useMemo(() => {
    if (!items) return null;
    const top: VNode[] = [];
    const index = new Map<string, VNode>();
    for (const it of items) {
      const segs = it.relpath.split('/').filter(Boolean);
      let parentPath = '';
      let siblings = top;
      segs.forEach((seg, depth) => {
        const path = parentPath ? `${parentPath}/${seg}` : seg;
        let node = index.get(path);
        if (!node) {
          node = { name: seg, path, isDir: depth < segs.length - 1 || it.is_dir, children: [] };
          index.set(path, node);
          siblings.push(node);
        }
        siblings = node.children;
        parentPath = path;
      });
    }
    const sortRec = (nodes: VNode[]) => {
      nodes.sort((a, b) =>
        a.isDir === b.isDir ? a.name.localeCompare(b.name) : a.isDir ? -1 : 1,
      );
      nodes.forEach((n) => sortRec(n.children));
    };
    sortRec(top);
    return top;
  }, [items]);

  const loadTree = async () => {
    setLoading(true);
    try {
      const r = await api.vaultTree();
      setItems(r.items ?? []);
      setRoot(r.root ?? '');
    } catch (e) {
      pushToast('error', `读取金库失败：${errText(e)}`, 8000);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card
      title="Obsidian 金库浏览"
      extra={
        <button className="btn btn-sm" onClick={() => void loadTree()} disabled={loading}>
          {loading && <Spinner size={12} />}
          {items ? '重新加载' : '加载目录树'}
        </button>
      }
    >
      {!items && !loading && <p className="muted small">点击右上角加载 vault 目录结构。</p>}
      {tree && (
        <>
          <NotePath path={root} />
          <div className="vault-tree">
            <VNodes nodes={tree} depth={0} />
          </div>
        </>
      )}
    </Card>
  );
}

function VNodes({ nodes, depth }: { nodes: VNode[]; depth: number }) {
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({});
  return (
    <>
      {nodes.map((n) => {
        const open = openMap[n.path] ?? depth < 1;
        return (
          <div key={n.path}>
            <div
              className={`vault-row${n.isDir ? ' is-dir' : ''}`}
              style={{ paddingLeft: depth * 16 }}
              onClick={() => n.isDir && setOpenMap((m) => ({ ...m, [n.path]: !open }))}
            >
              {n.isDir ? (
                <Icon name="folder" size={14} />
              ) : (
                <Icon name="file" size={14} />
              )}
              <span className="mono small">{n.name}</span>
            </div>
            {n.isDir && open && n.children.length > 0 && <VNodes nodes={n.children} depth={depth + 1} />}
          </div>
        );
      })}
    </>
  );
}
