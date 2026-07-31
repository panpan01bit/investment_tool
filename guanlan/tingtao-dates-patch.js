(function() {
  'use strict';

  const API_BASE = '';
  let manifestDates = [];
  let selectedDate = null;
  let chatMessages = [];

  async function fetchManifest() {
    try {
      const r = await fetch(`${API_BASE}/api/tingtao/manifest`);
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data.dates) ? data.dates : [];
    } catch (e) {
      console.error('[tingtao-patch] fetchManifest error:', e);
      return [];
    }
  }

  async function fetchDaily(date) {
    const r = await fetch(`${API_BASE}/api/tingtao/latest?date=${date}`);
    if (!r.ok) throw new Error(`status ${r.status}`);
    return await r.json();
  }

  function findElementByText(selector, text) {
    const els = document.querySelectorAll(selector);
    for (const el of els) {
      if (el.textContent.trim().includes(text)) return el;
    }
    return null;
  }

  function findSidebar() {
    const historyTitle = findElementByText('span', '日报历史');
    if (!historyTitle) return null;
    let sidebar = historyTitle.closest('[class*="liquid-glass"]');
    return sidebar || historyTitle.parentElement;
  }

  function findTitleSpan() {
    // 标题包含 "听涛日报" 或类似
    return findElementByText('span', '听涛日报') || findElementByText('span', '宏观早报');
  }

  function findContentDiv() {
    // 内容是 dangerouslySetInnerHTML 渲染的 div，特征：lineHeight 1.8, fontSize 13px，文本很长
    const allDivs = document.querySelectorAll('div');
    let best = null;
    let bestLen = 0;
    for (const div of allDivs) {
      const text = div.innerText || div.textContent || '';
      if (text.length > 500 && text.length > bestLen) {
        const style = window.getComputedStyle(div);
        if (style.lineHeight === '1.8' || parseFloat(style.lineHeight) > 1.6) {
          best = div;
          bestLen = text.length;
        }
      }
    }
    return best;
  }

  function findChatContainer() {
    const allDivs = document.querySelectorAll('div');
    for (const div of allDivs) {
      const text = div.innerText || div.textContent || '';
      if (text.includes('基于持仓和日报进行深度分析') && div.children.length > 0) {
        return div.parentElement;
      }
    }
    return null;
  }

  function renderHistoryDates(sidebar, onSelect) {
    let existing = sidebar.querySelector('#tingtao-dates-patch');
    if (existing) existing.remove();

    const wrapper = document.createElement('div');
    wrapper.id = 'tingtao-dates-patch';
    wrapper.style.cssText = 'padding:8px;border-top:1px solid rgba(255,255,255,0.06);';

    const title = document.createElement('div');
    title.textContent = '历史日期';
    title.style.cssText = 'font-size:11px;color:var(--text-secondary);margin-bottom:6px;padding:0 2px;';
    wrapper.appendChild(title);

    manifestDates.forEach(date => {
      const row = document.createElement('button');
      row.textContent = date;
      const isActive = date === selectedDate;
      row.style.cssText = `display:block;width:100%;text-align:left;padding:6px 10px;margin-bottom:4px;border-radius:6px;border:none;cursor:pointer;font-size:12px;background:${isActive ? 'rgba(74,124,89,0.15)' : 'rgba(255,255,255,0.04)'};color:${isActive ? 'var(--green)' : 'var(--text-primary)'};border:1px solid ${isActive ? 'rgba(74,124,89,0.3)' : 'transparent'};`;
      row.onmouseenter = () => { if (!isActive) row.style.background = 'rgba(255,255,255,0.08)'; };
      row.onmouseleave = () => { if (!isActive) row.style.background = 'rgba(255,255,255,0.04)'; };
      row.onclick = () => onSelect(date);
      wrapper.appendChild(row);
    });

    sidebar.appendChild(wrapper);
  }

  function updatePage(data) {
    // 标题
    const titleSpan = findTitleSpan();
    if (titleSpan) titleSpan.textContent = data.title || '听涛日报';

    // 内容
    const contentDiv = findContentDiv();
    if (contentDiv) {
      contentDiv.innerHTML = data.content || '';
    }

    // 更新侧边栏当前日期显示
    const todaySpan = findElementByText('span', '今日');
    if (todaySpan && todaySpan.previousElementSibling) {
      todaySpan.previousElementSibling.textContent = data.date || selectedDate;
    }

    // 清空聊天历史（简单处理）
    chatMessages = data.chat_history || [];
    const chatContainer = findChatContainer();
    if (chatContainer) {
      // 保留空状态提示
      chatContainer.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-secondary);font-size:13px">已切换日期，开始新的对话...</div>';
    }
  }

  async function onSelectDate(sidebar, date) {
    selectedDate = date;
    renderHistoryDates(sidebar, (d) => onSelectDate(sidebar, d));
    try {
      const data = await fetchDaily(date);
      updatePage(data);
    } catch (e) {
      console.error('[tingtao-patch] select date error:', e);
      alert('加载失败：' + e.message);
    }
  }

  async function init() {
    if (!window.location.pathname.includes('/tingtao') && !window.location.hash.includes('/tingtao')) return;

    manifestDates = await fetchManifest();
    if (manifestDates.length === 0) return;

    selectedDate = manifestDates[0];

    const tryInit = () => {
      const sidebar = findSidebar();
      if (!sidebar) return false;
      renderHistoryDates(sidebar, (date) => onSelectDate(sidebar, date));
      return true;
    };

    if (tryInit()) return;

    const obs = new MutationObserver(() => {
      if (tryInit()) obs.disconnect();
    });
    obs.observe(document.body, { childList: true, subtree: true });
    // 最多观察 10 秒
    setTimeout(() => obs.disconnect(), 10000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    setTimeout(init, 500);
  }
})();
