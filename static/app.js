/* PanSaver 管理后台 · 原生 JS，无构建步骤 */

const appEl = document.getElementById('app');
const modalRoot = document.getElementById('modal-root');
const toastRoot = document.getElementById('toast-root');

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* 用文字徽标代替 emoji：不依赖系统 emoji 字体，任何环境都不会变方块 */
const DRIVE_BADGE = {
  '115': ['115', '#3b82f6'],
  baidu: ['百', '#4f6bff'],
  quark: ['夸', '#8b5cf6'],
  xunlei: ['雷', '#0ea5e9'],
  mock: ['M', '#6b7280'],
};

const badge = (key) => {
  const [text, color] = DRIVE_BADGE[key] || ['?', '#6b7280'];
  return `<span class="dicon" style="color:${color};background:${color}26;border-color:${color}55">${esc(text)}</span>`;
};

const folderIcon = `<svg class="ficon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.2l1.8 2h8A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5v-9Z"/></svg>`;
const cloudIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" width="34" height="34"><path d="M7 18h9.5a3.5 3.5 0 0 0 .4-6.98A5.5 5.5 0 0 0 6.4 9.6 3.7 3.7 0 0 0 7 18Z"/></svg>`;
const DRIVE_NAME = { '115': '115 网盘', baidu: '百度网盘', quark: '夸克网盘', xunlei: '迅雷网盘', mock: 'Mock 网盘' };
const COOKIE_HINT = {
  '115': '在浏览器登录 115 后，F12 → Application → Cookies，复制 UID、CID、SEID、KID 等字段，粘贴为 UID=..; CID=..; SEID=..; KID=..',
  baidu: '登录 pan.baidu.com 后复制 Cookie 中的 BDUSS、STOKEN（可选 BAIDUID、PTOKEN），粘贴为 BDUSS=..; STOKEN=..',
  quark: '登录 pan.quark.cn 后复制 Cookie 中的 __pus、__puus（可选 __kp、__uid），粘贴为 __pus=..; __puus=..',
  xunlei: '迅雷扫码接口已下线（2026-11 实测），请导入凭证：① 浏览器登录 pan.xunlei.com；② F12 → Application → Local Storage → https://pan.xunlei.com → 复制 credentials_Xqp0kJBXWhwaTpB6 的值（一段 JSON）；③ 整段粘贴到下面。也接受只含 access_token 或 refresh_token 的 JSON。',
  mock: 'Mock 网盘请直接点「扫码登录」，3 秒后自动成功。',
};

const S = {
  status: null,
  drives: [],
  settings: null,
  stats: null,
  tab: 'drives',
  logs: { page: 1, size: 20, drive: '', status: '', q: '', total: 0 },
  pollTimer: null,
  dirStack: [],
};

/* ---------------- 基础工具 ---------------- */
async function api(path, { method = 'GET', body, raw = false } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    S.status = { setup_required: false, authenticated: false };
    renderAuth(false);
    throw new Error('登录已失效，请重新登录');
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function toast(message, kind = '') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  toastRoot.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function openModal(html) {
  modalRoot.innerHTML = `<div class="modal-mask"><div class="modal">${html}</div></div>`;
  modalRoot.querySelector('.modal-mask').addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-mask')) closeModal();
  });
  return modalRoot.querySelector('.modal');
}

function closeModal() {
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  modalRoot.innerHTML = '';
}

function botPill() {
  const st = S.status || {};
  if (!st.bot_ready) return 'Bot 未配置';
  if (st.bot_running) return `Bot 在线${st.bot_username ? ' @' + st.bot_username : ''}`;
  return 'Bot 异常（看日志）';
}

const fmtTime = (iso) => {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

/* ---------------- 启动 ---------------- */
async function boot() {
  try {
    S.status = await api('/api/auth/status');
  } catch (err) {
    appEl.innerHTML = `<div class="boot">无法连接服务：${esc(err.message)}</div>`;
    return;
  }
  if (S.status.setup_required) return renderAuth(true);
  if (!S.status.authenticated) return renderAuth(false);
  await renderApp();
}

/* ---------------- 登录 / 初始化 ---------------- */
function renderAuth(setupRequired) {
  appEl.innerHTML = `
    <div class="auth-wrap">
      <form class="auth-card" id="auth-form">
        <div class="logo" style="color:var(--accent)">${cloudIcon}</div>
        <h1>${setupRequired ? '初始化管理员' : 'PanSaver 后台登录'}</h1>
        <p class="sub">${setupRequired ? '首次使用，请设置后台账号与密码（密码至少 8 位，不能纯数字）' : '请使用管理员账号登录'}</p>
        <div class="field"><label>用户名</label><input type="text" name="username" autocomplete="username" required /></div>
        <div class="field"><label>密码</label><input type="password" name="password" autocomplete="${setupRequired ? 'new-password' : 'current-password'}" required /></div>
        ${setupRequired ? `<div class="field"><label>确认密码</label><input type="password" name="confirm" autocomplete="new-password" required /></div>
        <div class="field"><label>初始化口令<span class="hint">（服务器设置了 SETUP_TOKEN 时才需要，否则留空）</span></label><input type="text" name="setup_token" autocomplete="off" placeholder="留空即可" /></div>` : ''}
        <button class="btn primary" style="width:100%;padding:11px" type="submit">${setupRequired ? '创建并进入' : '登录'}</button>
        <p class="hint" style="margin-top:14px">${setupRequired ? '这个账号只用于后台管理，与网盘账号无关。' : '忘记密码？删除 data/pansaver.db 会清空全部数据（含日志）。'}</p>
      </form>
    </div>`;

  document.getElementById('auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const username = form.username.value.trim();
    const password = form.password.value;
    if (setupRequired && password !== form.confirm.value) return toast('两次输入的密码不一致', 'err');
    const btn = form.querySelector('button');
    btn.disabled = true;
    try {
      const payload = { username, password };
      const tokenInput = form.setup_token;
      if (setupRequired && tokenInput && tokenInput.value.trim()) payload.setup_token = tokenInput.value.trim();
      await api(setupRequired ? '/api/auth/setup' : '/api/auth/login', { method: 'POST', body: payload });
      S.status = await api('/api/auth/status');
      await renderApp();
    } catch (err) {
      toast(err.message, 'err');
      btn.disabled = false;
    }
  });
}

/* ---------------- 主框架 ---------------- */
async function renderApp() {
  appEl.innerHTML = `
    <div class="shell">
      <div class="topbar">
        <div class="brand"><span class="dot ${S.status.bot_ready ? '' : 'off'}"></span>PanSaver</div>
        <div class="tabs">
          <button class="tab" data-tab="drives">网盘</button>
          <button class="tab" data-tab="logs">日志</button>
          <button class="tab" data-tab="settings">设置</button>
        </div>
        <div class="spacer"></div>
        <span class="pill mute" id="bot-pill">${botPill()}</span>
        <button class="btn sm ghost" id="logout">退出</button>
      </div>
      <div id="view"></div>
    </div>`;

  document.getElementById('logout').addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' });
    S.status = { setup_required: false, authenticated: false };
    renderAuth(false);
  });

  appEl.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  await switchTab('drives');
}

async function switchTab(tab) {
  S.tab = tab;
  appEl.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
  const view = document.getElementById('view');
  if (tab === 'drives') await renderDrives(view);
  if (tab === 'logs') await renderLogs(view);
  if (tab === 'settings') await renderSettings(view);
}

/* ---------------- 网盘页 ---------------- */
async function renderDrives(view) {
  view.innerHTML = `<div class="empty">读取网盘状态…</div>`;
  try {
    const [drives, stats] = await Promise.all([api('/api/drives'), api('/api/logs/stats')]);
    S.drives = drives.items;
    S.stats = stats;
  } catch (err) {
    view.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
    return;
  }

  const cards = S.drives.map((d) => {
    const bound = d.bound && d.status !== 'unbound';
    const pill = d.status === 'ok' ? ['ok', '已绑定'] : d.status === 'expired' ? ['warn', '凭证失效'] : d.status === 'error' ? ['err', '状态异常'] : ['mute', '未绑定'];
    return `
      <div class="card">
        <div class="card-head">
          <span class="ico">${badge(d.key)}</span>
          <span class="name">${esc(d.name)}</span>
          <span class="spacer"></span>
          <span class="pill ${pill[0]}">${pill[1]}</span>
        </div>
        <div class="meta">
          <div>账号：${esc(d.account_name || (bound ? '已绑定' : '—'))}</div>
          <div>默认目录：${esc(d.default_dir_name || '根目录')}</div>
          ${d.status_message ? `<div style="color:var(--warn)">状态：${esc(d.status_message)}</div>` : ''}
          <div>更新：${d.updated_at ? fmtTime(d.updated_at) : '—'}</div>
        </div>
        <div class="actions">
          <button class="btn primary sm" data-action="qr" data-drive="${d.key}">扫码登录</button>
          <button class="btn sm" data-action="cookie" data-drive="${d.key}">Cookie 导入</button>
          <button class="btn sm" data-action="dirs" data-drive="${d.key}">目录 / 默认位置</button>
          ${bound ? `<button class="btn sm" data-action="check" data-drive="${d.key}">校验</button>
                     <button class="btn danger sm" data-action="unbind" data-drive="${d.key}">解绑</button>` : ''}
        </div>
      </div>`;
  }).join('');

  const st = S.stats || {};
  view.innerHTML = `
    <div class="stats">
      <div class="stat"><div class="k">今日转存</div><div class="v">${st.today ?? 0}</div></div>
      <div class="stat"><div class="k">今日成功</div><div class="v" style="color:var(--ok)">${st.today_success ?? 0}</div></div>
      <div class="stat"><div class="k">今日失败</div><div class="v" style="color:var(--err)">${st.today_failed ?? 0}</div></div>
      <div class="stat"><div class="k">成功率</div><div class="v">${st.success_rate ?? 0}%</div></div>
      <div class="stat"><div class="k">累计转存</div><div class="v">${st.total ?? 0}</div></div>
    </div>
    <div class="section-title">网盘绑定</div>
    <div class="grid">${cards || '<div class="empty">没有可用网盘</div>'}</div>
    <div class="hint" style="margin-top:14px">提示：扫码登录后凭证保存在本机数据库（data/pansaver.db），不会上传到任何第三方服务。</div>`;

  view.querySelectorAll('[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => handleDriveAction(btn.dataset.action, btn.dataset.drive));
  });
}

async function handleDriveAction(action, drive) {
  if (action === 'qr') return openQr(drive);
  if (action === 'cookie') return openCookie(drive);
  if (action === 'dirs') return openDirs(drive);
  if (action === 'check') {
    try {
      const res = await api(`/api/drives/${drive}/check`, { method: 'POST' });
      toast(res.ok ? `凭证有效（${res.account_name || drive}）` : res.message, res.ok ? 'ok' : 'err');
      await renderDrives(document.getElementById('view'));
    } catch (err) {
      toast(err.message, 'err');
    }
    return;
  }
  if (action === 'unbind') {
    if (!confirm(`确定解绑 ${DRIVE_NAME[drive] || drive}？凭证会被删除，需重新登录。`)) return;
    try {
      await api(`/api/drives/${drive}`, { method: 'DELETE' });
      toast('已解绑', 'ok');
      await renderDrives(document.getElementById('view'));
    } catch (err) {
      toast(err.message, 'err');
    }
  }
}

/* ---------------- 扫码 ---------------- */
async function openQr(drive) {
  let start;
  try {
    start = await api(`/api/drives/${drive}/qr`, { method: 'POST' });
  } catch (err) {
    openCookie(drive, `扫码不可用：${err.message}`);
    return;
  }
  const imgSrc = start.mode === 'image'
    ? `/api/drives/qr-image?url=${encodeURIComponent(start.image_url)}`
    : `/api/drives/${drive}/qr/${start.qr_id}/png`;

  const modal = openModal(`
    <h3>${esc(DRIVE_NAME[drive] || drive)} · 扫码登录</h3>
    <div class="modal-sub">${esc(start.tip || '用对应 App 扫码')}</div>
    <div class="qr-box"><img id="qr-img" src="${imgSrc}" alt="二维码" /></div>
    <div class="qr-status" id="qr-status">等待扫码…</div>
    ${start.content ? `<p class="hint" style="margin-top:10px;word-break:break-all">链接：<a href="${esc(start.content)}" target="_blank" rel="noreferrer">${esc(start.content)}</a></p>` : ''}
    <div class="modal-foot"><button class="btn ghost" id="qr-cancel">关闭</button></div>`);

  modal.querySelector('#qr-cancel').addEventListener('click', closeModal);
  const statusEl = modal.querySelector('#qr-status');
  const interval = Math.max(1, start.interval || 2) * 1000;

  modal.querySelector('#qr-img').addEventListener('error', () => {
    if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
    closeModal();
    toast('二维码加载失败，已切换为 Cookie 导入', 'err');
    openCookie(drive, '扫码二维码加载失败（可能是上游接口变化），请用 Cookie 导入');
  });

  let inflight = false;
  S.pollTimer = setInterval(async () => {
    if (inflight) return;  // 上一次还没回来就不叠加，避免请求堆积
    inflight = true;
    try {
      const res = await api(`/api/drives/${drive}/qr/${start.qr_id}`);
      if (res.status === 'success') {
        clearInterval(S.pollTimer); S.pollTimer = null;
        statusEl.textContent = '登录成功，正在刷新…';
        setTimeout(async () => {
          closeModal();
          toast(`${DRIVE_NAME[drive] || drive} 绑定成功`, 'ok');
          await renderDrives(document.getElementById('view'));
        }, 600);
      } else if (res.status === 'expired' || res.status === 'failed') {
        clearInterval(S.pollTimer); S.pollTimer = null;
        statusEl.textContent = `失败：${res.message || '登录失败'}`;
      } else {
        statusEl.textContent = res.status === 'scanned' ? `已扫码：${res.message || '请在手机上确认'}` : (res.message || '等待扫码…');
      }
    } catch (err) {
      // 网络抖动/隧道抖一下就继续等，不要把用户吓回 Cookie 导入
      statusEl.textContent = `网络抖动，继续等待…（${err.message}）`;
    } finally {
      inflight = false;
    }
  }, interval);
}

/* ---------------- Cookie 导入 ---------------- */
function openCookie(drive, extraNote = '') {
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  const modal = openModal(`
    <h3>${esc(DRIVE_NAME[drive] || drive)} · ${drive === 'xunlei' ? '导入凭证' : '导入 Cookie'}</h3>
    <div class="modal-sub">${extraNote ? `<span style="color:var(--warn)">${esc(extraNote)}</span><br/>` : ''}${esc(COOKIE_HINT[drive] || '')}</div>
    <div class="field">
      <label>${drive === 'xunlei' ? '凭证（JSON 或 access_token）' : 'Cookie'}</label>
      <textarea id="cookie-input" placeholder="${drive === 'xunlei' ? '{"token":"...","refresh_token":"..."}' : 'k1=v1; k2=v2'}"></textarea>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" id="cookie-cancel">取消</button>
      <button class="btn primary" id="cookie-save">保存并校验</button>
    </div>`);

  modal.querySelector('#cookie-cancel').addEventListener('click', closeModal);
  modal.querySelector('#cookie-save').addEventListener('click', async (e) => {
    const value = modal.querySelector('#cookie-input').value.trim();
    if (!value) return toast('请先粘贴 Cookie', 'err');
    e.target.disabled = true;
    try {
      const res = await api(`/api/drives/${drive}/cookie`, { method: 'POST', body: { cookie: value } });
      toast(res.ok ? `绑定成功：${res.account_name || drive}` : `已保存，但校验未通过：${res.message}`, res.ok ? 'ok' : 'err');
      if (res.ok) {
        closeModal();
        await renderDrives(document.getElementById('view'));
      }
    } catch (err) {
      toast(err.message, 'err');
    } finally {
      e.target.disabled = false;
    }
  });
}

/* ---------------- 目录浏览 / 默认目录 ---------------- */
async function openDirs(drive) {
  S.dirStack = [{ id: '0', name: '根目录' }];
  const modal = openModal(`
    <h3>${esc(DRIVE_NAME[drive] || drive)} · 目录</h3>
    <div class="modal-sub">点文件夹进入子目录；点「设为默认」把当前目录存为默认保存位置。</div>
    <div class="breadcrumb" id="crumb"></div>
    <div class="dir-list" id="dir-list"><div class="empty">加载中…</div></div>
    <div class="modal-foot">
      <button class="btn ghost" id="dir-close">关闭</button>
      <button class="btn primary" id="dir-set-default">把当前目录设为默认</button>
    </div>`);

  modal.querySelector('#dir-close').addEventListener('click', closeModal);

  const load = async () => {
    const current = S.dirStack[S.dirStack.length - 1];
    const crumb = modal.querySelector('#crumb');
    crumb.innerHTML = S.dirStack
      .map((node, idx) => (idx === S.dirStack.length - 1
        ? `<span>${esc(node.name)}</span>`
        : `<button data-idx="${idx}">${esc(node.name)}</button> / `))
      .join('');
    crumb.querySelectorAll('button').forEach((b) => b.addEventListener('click', async () => {
      S.dirStack = S.dirStack.slice(0, Number(b.dataset.idx) + 1);
      await load();
    }));

    const list = modal.querySelector('#dir-list');
    list.innerHTML = '<div class="empty">加载中…</div>';
    try {
      const res = await api(`/api/drives/${drive}/dirs?parent_id=${encodeURIComponent(current.id)}`);
      if (!res.items.length) {
        list.innerHTML = '<div class="empty">该目录下没有子目录</div>';
        return;
      }
      list.innerHTML = res.items.map((item, idx) => `
        <div class="dir-item">
          <span class="dir-name" data-idx="${idx}">${folderIcon} ${esc(item.name)}</span>
          <button class="btn sm" data-default="${idx}">设为默认</button>
        </div>`).join('');
      list.querySelectorAll('.dir-name').forEach((n) => n.addEventListener('click', async () => {
        const item = res.items[Number(n.dataset.idx)];
        S.dirStack.push({ id: item.id, name: item.name });
        await load();
      }));
      list.querySelectorAll('[data-default]').forEach((b) => b.addEventListener('click', async () => {
        const item = res.items[Number(b.dataset.default)];
        await setDefaultDir(drive, item.id, item.name);
      }));
    } catch (err) {
      list.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
  };

  modal.querySelector('#dir-set-default').addEventListener('click', async () => {
    const current = S.dirStack[S.dirStack.length - 1];
    await setDefaultDir(drive, current.id, current.name);
  });

  await load();
}

async function setDefaultDir(drive, dirId, dirName) {
  try {
    await api(`/api/drives/${drive}/default-dir`, { method: 'PUT', body: { dir_id: dirId, dir_name: dirName } });
    toast(`默认目录已设为「${dirName}」`, 'ok');
    closeModal();
    await renderDrives(document.getElementById('view'));
  } catch (err) {
    toast(err.message, 'err');
  }
}

/* ---------------- 日志页 ---------------- */
async function renderLogs(view) {
  const q = S.logs;
  view.innerHTML = `
    <div class="row" style="margin-bottom:14px">
      <select id="filter-drive" style="max-width:140px">
        <option value="">全部网盘</option>
        ${Object.entries(DRIVE_NAME).map(([k, v]) => `<option value="${k}" ${q.drive === k ? 'selected' : ''}>${v}</option>`).join('')}
      </select>
      <select id="filter-status" style="max-width:130px">
        <option value="">全部状态</option>
        <option value="success" ${q.status === 'success' ? 'selected' : ''}>成功</option>
        <option value="failed" ${q.status === 'failed' ? 'selected' : ''}>失败</option>
      </select>
      <input type="text" id="filter-q" placeholder="搜索链接 / 目录 / 消息" value="${esc(q.q)}" style="max-width:280px" />
      <button class="btn sm" id="filter-go">刷新</button>
      <div class="spacer" style="flex:1"></div>
      <span class="hint">共 ${q.total} 条</span>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>时间</th><th>网盘</th><th>状态</th><th>目录</th><th>文件</th><th>耗时</th><th>来源</th><th>消息</th><th>链接</th></tr></thead>
      <tbody id="log-body"><tr><td colspan="9" class="empty">加载中…</td></tr></tbody>
    </table></div>
    <div class="pager">
      <button class="btn sm" id="prev" ${q.page <= 1 ? 'disabled' : ''}>上一页</button>
      <span>第 ${q.page} 页</span>
      <button class="btn sm" id="next" ${q.page * q.size >= q.total ? 'disabled' : ''}>下一页</button>
    </div>`;

  view.querySelector('#filter-go').addEventListener('click', () => {
    S.logs.drive = view.querySelector('#filter-drive').value;
    S.logs.status = view.querySelector('#filter-status').value;
    S.logs.q = view.querySelector('#filter-q').value.trim();
    S.logs.page = 1;
    renderLogs(view);
  });
  view.querySelector('#prev').addEventListener('click', () => { S.logs.page -= 1; renderLogs(view); });
  view.querySelector('#next').addEventListener('click', () => { S.logs.page += 1; renderLogs(view); });

  const params = new URLSearchParams({ page: q.page, size: q.size });
  if (q.drive) params.set('drive', q.drive);
  if (q.status) params.set('status', q.status);
  if (q.q) params.set('q', q.q);

  try {
    const data = await api(`/api/logs?${params.toString()}`);
    S.logs.total = data.total;
    const body = view.querySelector('#log-body');
    if (!data.items.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty">暂无记录</td></tr>';
    } else {
      body.innerHTML = data.items.map((row) => `
        <tr>
          <td>${fmtTime(row.created_at)}</td>
          <td><span class="dcell">${badge(row.drive)} ${esc(DRIVE_NAME[row.drive] || row.drive)}</span></td>
          <td><span class="pill ${row.status === 'success' ? 'ok' : 'err'}">${row.status === 'success' ? '成功' : '失败'}</span></td>
          <td>${esc(row.dir_name || (row.dir_id === '0' ? '根目录' : `目录 ${row.dir_id}`))}</td>
          <td>${row.files}</td>
          <td>${row.duration_ms}ms</td>
          <td>${row.source === 'bot' ? '@' + esc(row.tg_username || row.tg_user_id || 'bot') : esc(row.source)}</td>
          <td class="wrap">${esc(row.message)}</td>
          <td class="wrap"><a href="${esc(row.share_url)}" target="_blank" rel="noreferrer">${esc(row.share_url.slice(0, 48))}</a></td>
        </tr>`).join('');
    }
    view.querySelector('.hint').textContent = `共 ${data.total} 条`;
    view.querySelector('#prev').disabled = q.page <= 1;
    view.querySelector('#next').disabled = q.page * q.size >= data.total;
  } catch (err) {
    view.querySelector('#log-body').innerHTML = `<tr><td colspan="9" class="empty">${esc(err.message)}</td></tr>`;
  }
}

/* ---------------- 设置页 ---------------- */
async function renderSettings(view) {
  view.innerHTML = `<div class="empty">加载设置…</div>`;
  let data;
  try {
    data = await api('/api/settings');
    S.settings = data;
  } catch (err) {
    view.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }

  const allows = data.allows.length
    ? data.allows.map((a) => `
        <div class="list-row">
          <span class="grow">${esc(a.username ? '@' + a.username : '')} <span class="tag mono">${a.tg_user_id}</span> ${esc(a.note || '')}</span>
          <button class="btn sm" data-toggle="${a.id}" data-enabled="${a.enabled}">${a.enabled ? '停用' : '启用'}</button>
          <button class="btn danger sm" data-del="${a.id}">删除</button>
        </div>`).join('')
    : '<div class="empty">白名单为空：Bot 会拒绝所有人，请先加入自己的 Telegram ID</div>';

  view.innerHTML = `
    <div class="card">
      <div class="card-head"><span class="name">Telegram Bot</span><span class="spacer"></span>
        <span class="pill ${data.bot_ready ? 'ok' : 'warn'}">${data.bot_ready ? '已配置' : '未配置'}</span>
      </div>
      <div class="meta">
        <div>Token：<span class="mono">${esc(data.bot_token_masked || '未设置')}</span></div>
        <div class="hint">Token 通过环境变量 TELEGRAM_BOT_TOKEN 配置，修改后重启容器生效。首次启动请给你的 Bot 发 /start 获取自己的 Telegram ID。</div>
      </div>
      <div class="row">
        <input type="text" id="allow-id" placeholder="Telegram 用户 ID（纯数字）" style="max-width:220px" />
        <input type="text" id="allow-note" placeholder="备注（可选）" style="max-width:220px" />
        <button class="btn primary sm" id="allow-add">加入白名单</button>
      </div>
    </div>

    <div class="section-title">Bot 白名单</div>
    <div class="list" id="allow-list">${allows}</div>

    <div class="section-title">修改后台密码</div>
    <form class="card" id="pwd-form">
      <div class="field"><label>原密码</label><input type="password" name="old" required /></div>
      <div class="field"><label>新密码</label><input type="password" name="new" required /></div>
      <button class="btn primary sm" type="submit">保存</button>
      <span class="hint" style="margin-left:10px">保存后其它登录会话会失效</span>
    </form>

    <div class="section-title">关于</div>
    <div class="card">
      <div class="meta" style="margin:0">
        <div>版本：${esc((S.status && S.status.app_name) || 'PanSaver')} · 0.1.0</div>
        <div>支持的网盘：${data.drives.map((d) => esc(d.name)).join('、')}</div>
        <div class="hint">接口签名随时可能被官方调整，如遇报错请先「校验」凭证，再看日志里的具体原因。</div>
      </div>
    </div>`;

  view.querySelector('#allow-add').addEventListener('click', async () => {
    const id = view.querySelector('#allow-id').value.trim();
    if (!/^\d+$/.test(id)) return toast('用户 ID 必须是纯数字', 'err');
    try {
      await api('/api/settings/allows', { method: 'POST', body: { tg_user_id: Number(id), note: view.querySelector('#allow-note').value.trim() } });
      toast('已加入白名单', 'ok');
      await renderSettings(view);
    } catch (err) {
      toast(err.message, 'err');
    }
  });

  view.querySelectorAll('[data-toggle]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/settings/allows/${b.dataset.toggle}`, { method: 'PATCH', body: { enabled: b.dataset.enabled !== 'true' } });
    await renderSettings(view);
  }));
  view.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/settings/allows/${b.dataset.del}`, { method: 'DELETE' });
    await renderSettings(view);
  }));

  view.querySelector('#pwd-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    try {
      await api('/api/auth/password', { method: 'POST', body: { old_password: form.old.value, new_password: form.new.value } });
      form.reset();
      toast('密码已更新', 'ok');
    } catch (err) {
      toast(err.message, 'err');
    }
  });
}

boot();
