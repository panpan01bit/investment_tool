(function() {
  'use strict';

  const API_BASE = '';

  function findElementByText(selector, text) {
    const els = document.querySelectorAll(selector);
    for (const el of els) {
      if (el.textContent.trim().includes(text)) return el;
    }
    return null;
  }

  function showToast(message, type = 'info') {
    const existing = document.getElementById('holdings-upload-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'holdings-upload-toast';
    const color = type === 'success' ? 'rgba(74,124,89,0.9)' : type === 'error' ? 'rgba(196,90,90,0.9)' : 'rgba(60,60,60,0.9)';
    toast.style.cssText = `position:fixed;top:20px;right:20px;z-index:10000;padding:12px 18px;border-radius:8px;color:white;font-size:13px;background:${color};box-shadow:0 4px 12px rgba(0,0,0,0.3);transition:opacity 0.3s;`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  async function uploadHoldings(file) {
    if (!file) return;
    if (!file.name.endsWith('.xlsx') && !file.name.endsWith('.xls')) {
      showToast('请上传 .xlsx 或 .xls 持仓表', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    showToast('正在上传持仓表...', 'info');
    try {
      const res = await fetch(`${API_BASE}/api/upload-holdings`, {
        method: 'POST',
        body: formData,
        credentials: 'include'
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        showToast(data.message || `持仓表上传成功：${data.count} 条`, 'success');
      } else if (res.status === 401) {
        showToast('上传失败：请先登录管理员账号', 'error');
      } else if (res.status === 403) {
        showToast('上传失败：需要管理员权限', 'error');
      } else {
        showToast(`上传失败：${data.error || res.statusText}`, 'error');
      }
    } catch (e) {
      console.error('[holdings-upload-patch] upload error:', e);
      showToast('上传失败：网络错误', 'error');
    }
  }

  function hookUploadInput() {
    // 找到听涛页面里的文件 input（通常在 "上传持仓文件" / "上传文件" 按钮附近）
    const inputs = document.querySelectorAll('input[type="file"]');
    for (const input of inputs) {
      if (input.dataset.holdingsHooked) continue;

      // 只处理在 /tingtao 路径下的上传 input
      if (!window.location.pathname.includes('/tingtao')) continue;

      input.dataset.holdingsHooked = 'true';

      input.addEventListener('change', function(e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;

        // 通过文案判断：找到触发按钮或附近的文本，确认是持仓上传而非解析材料
        // 如果文件名包含 holdings / 持仓 / 模板，则走持仓接口；否则走原逻辑
        const nameLower = file.name.toLowerCase();
        const isHoldings = nameLower.includes('holdings') ||
                           nameLower.includes('持仓') ||
                           nameLower.includes('模板') ||
                           nameLower.includes('portfolio') ||
                           nameLower.endsWith('.xlsx') ||
                           nameLower.endsWith('.xls');

        if (!isHoldings) return;

        // 阻止后续事件（React onChange 已经触发，但我们可以先调用上传）
        e.stopImmediatePropagation();
        e.preventDefault();

        uploadHoldings(file);

        // 清空 input，避免再次选择同一文件时不触发 change
        input.value = '';
      }, true);
    }
  }

  function init() {
    if (!window.location.pathname.includes('/tingtao')) return;

    hookUploadInput();

    const obs = new MutationObserver(() => {
      hookUploadInput();
    });
    obs.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => obs.disconnect(), 15000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    setTimeout(init, 500);
  }
})();
