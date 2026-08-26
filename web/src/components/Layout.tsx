import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { api } from '../lib/api';
import { Icon } from './icons';
import Toasts from './Toasts';

const NAV = [
  { to: '/', label: '每日简报', icon: 'daily', end: true },
  { to: '/portfolio', label: '持仓组合', icon: 'case', end: false },
  { to: '/research', label: '个股研究', icon: 'target', end: false },
  { to: '/quant', label: '量化信号', icon: 'chart', end: false },
  { to: '/reports', label: '报告库', icon: 'book', end: false },
  { to: '/chat', label: '研究问答', icon: 'chat', end: false },
  { to: '/settings', label: '设置', icon: 'sliders', end: false },
];

export default function Layout() {
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let stopped = false;
    const ping = async () => {
      try {
        const r = await api.health();
        if (!stopped) setHealthy(Boolean(r?.ok));
      } catch {
        if (!stopped) setHealthy(false);
      }
    };
    void ping();
    const timer = window.setInterval(ping, 30_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-mark">澜</div>
          <div className="logo-text">
            <strong>观澜 GUANLAN</strong>
            <span>InvestLab v2 · 本地投研工作台</span>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <footer className="side-foot">
          <span className={`dot${healthy === false ? ' dot-bad' : healthy === true ? ' dot-ok' : ''}`} />
          <span className="side-foot-text">
            {healthy === null ? '检测后端服务…' : healthy ? '后端在线 · 127.0.0.1:8300' : '后端离线 · 127.0.0.1:8300'}
          </span>
        </footer>
      </aside>
      <main className="main">
        <Outlet />
      </main>
      <Toasts />
    </div>
  );
}
