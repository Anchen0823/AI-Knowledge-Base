'use strict';

/* ---------------------------------------------------------------------------
   私人知识库前端：单文件，无构建步骤。
   页面数据只有两个来源：/api/state（四个视图共用的状态）和 /api/graph（知识图谱）。
   所有接口都需要启动时生成的口令，放在 Authorization 头里。
   --------------------------------------------------------------------------- */

let key = new URLSearchParams(location.hash.slice(1)).get('key') || sessionStorage.getItem('kb-key') || '';
if (location.hash) history.replaceState(null, '', location.pathname);

let state = null;
let view = 'encyclopedia';
let selectedEntry = null;
let lastSnapshot = '';
let noticeTimer = null;

const $ = (selector) => document.querySelector(selector);

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function button(text, onClick, cls = 'secondary small') {
  const node = el('button', text, cls);
  node.type = 'button';
  node.onclick = onClick;
  return node;
}

function externalLink(text, href, cls) {
  const node = el('a', text, cls);
  node.href = href;
  node.target = '_blank';
  node.rel = 'noreferrer noopener';
  return node;
}

function fail(message, status) {
  const error = new Error(message);
  error.status = status;
  return error;
}

/* ----------------------------- 文案 ----------------------------- */

const VIEW_TITLE = { encyclopedia: '百科', shelf: '书架', map: '知识地图', pending: '待处理' };

/* 任务状态：一句人话 + 一个色调（busy 进行中 / ready 等操作 / paused 需处理）。 */
const JOB_STATUS = {
  preparing: ['正在准备材料', 'busy'],
  extracting: ['正在识别画面文字', 'busy'],
  recognizing: ['正在识别画面文字', 'busy'],
  ingesting: ['正在获取视频和画面', 'busy'],
  prepared: ['材料已备好，还没开始分析', 'ready'],
  analyzing: ['正在分析画面和文字，页面会自动更新', 'busy'],
  analyzed: ['分析完成，正在等发布检查', 'ready'],
  paused: ['已暂停，需要你看一下原因', 'paused'],
};

const JOB_ACTION = { prepared: '开始分析', paused: '重新尝试', analyzed: '重新检查' };

/* 概念类型是概念整理时写下的标签，这里换成更好懂的说法。 */
const CONCEPT_KIND = {
  游戏规则: '规则',
  算法策略: '策略',
  算法: '算法',
  算法构造: '算法',
  策略机制: '机制',
  数学概念: '数学',
  数学论证: '数学',
  评测结果: '评测',
  评测方法: '评测',
  程序版本: '程序',
  模型版本: '模型',
  性能参考: '性能参考',
  设计原则: '原则',
  交互产品: '产品',
  版权信息: '版权',
  资料呈现: '资料说明',
  未明确类别: '类型未注明',
};

/* 笔记里的三层表述，换成读者能直接看懂的分组名。 */
const CLAIM_GROUP = {
  原作者陈述: ['作者在视频里的说法', '直接来自视频原话，每条后面都标了时间点。'],
  整理者分析: ['整理时补的判断', '整理过程中加的推断，不是作者本人的结论。'],
  来源局限: ['这份资料的局限', '作者没证明、没说清或口径不同的地方，读结论前先看这里。'],
};

/* ----------------------------- 请求 ----------------------------- */

async function api(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: body ? 'POST' : 'GET',
      headers: { Authorization: 'Bearer ' + key, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    throw fail('连不上本地服务。请确认启动窗口还开着，然后刷新页面。', 0);
  }
  let data = null;
  try { data = await response.json(); } catch (error) { data = null; }
  if (!response.ok) {
    const message = data && data.error ? data.error : `请求没有成功（HTTP ${response.status}）。刷新页面可以再试一次。`;
    throw fail(message, response.status);
  }
  if (data && typeof data === 'object') data.status = response.status;
  return data;
}

/* ----------------------------- 小工具 ----------------------------- */

function notice(text, tone = 'info') {
  const box = $('#notice');
  clearTimeout(noticeTimer);
  if (!text) {
    box.hidden = true;
    box.textContent = '';
    return;
  }
  box.textContent = text;
  box.className = 'notice ' + tone;
  box.title = '点击关闭';
  box.hidden = false;
  box.onclick = () => notice('');
  if (tone !== 'error') noticeTimer = setTimeout(() => notice(''), 9000);
}

function showLoginError(message) {
  const box = $('#login-error');
  box.textContent = message || '';
  box.hidden = !message;
}

function formatDuration(seconds) {
  if (!seconds || !isFinite(seconds)) return '';
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function formatDate(stamp) {
  if (!stamp) return '';
  const date = new Date(stamp * 1000);
  if (isNaN(date.getTime())) return '';
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function sourceMeta(item) {
  const source = item.source || {};
  return ['视频', source.author, formatDuration(source.duration), source.published_at ? formatDate(source.published_at) + ' 入库' : '']
    .filter(Boolean).join(' · ');
}

/* 取笔记「概览」的第一段，给书架卡片当摘要用。 */
function overviewOf(notes) {
  const overview = parseNote(notes || '').overview;
  const text = overview.length ? overview[0] : String(notes || '').replace(/^#.*$/gm, '').replace(/[*`>-]/g, '');
  return text.replace(/[*`]/g, '').replace(/\s+/g, ' ').trim().slice(0, 150);
}

function pendingCount() {
  if (!state) return 0;
  return (state.jobs ? state.jobs.length : 0) + (state.queued ? state.queued.length : 0);
}

function contextLine() {
  if (!state) return '';
  const count = state.library ? state.library.length : 0;
  if (view === 'pending') {
    const waiting = pendingCount();
    return waiting ? `${waiting} 条还在整理或需要处理` : '没有待处理的任务';
  }
  if (!count) return '还没有正式入库的资料';
  if (view === 'shelf') return `${count} 份已入库的资料，点开就读`;
  if (view === 'map') return `${count} 份资料合在一张图上，共 ${count} 个来源节点`;
  return `${count} 份资料 · 正在看《${state.formal ? state.formal.title : ''}》`;
}

/* ----------------------------- 笔记解析 ----------------------------- */

/* 笔记由 snakekb.pipeline 生成，章节固定：
   概览 / 分段摘要 / 作者简介补充 / 详细笔记与条件 / 来源定位。
   这里把「条件：… 出处：…」这种给机器看的结构拆成能读的块；解析不了的章节按段落原样显示。 */
const SECTIONS = {
  概览: 'overview',
  分段摘要: 'summary',
  作者简介补充的条件与评测范围: 'supplements',
  详细笔记与条件: 'claims',
  来源定位: 'locations',
};

function parseNote(text) {
  const doc = { overview: [], summary: [], supplements: [], extras: [], claims: [], audits: [], locations: {} };
  let role = null;
  let current = null;
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\s+$/, '');
    const heading = /^##\s+(.+)$/.exec(line);
    if (heading) {
      const title = heading[1].trim();
      role = SECTIONS[title] || 'extras';
      if (role === 'extras') doc.extras.push({ title, paragraphs: [] });
      current = null;
      continue;
    }
    if (!role || /^#\s/.test(line)) continue;

    if (role === 'locations') {
      const spot = /^-\s+`([^`]+)`：\[([^\]]+)\]\(([^)]+)\)/.exec(line);
      if (spot) doc.locations[spot[1]] = { label: spot[2], href: spot[3] };
      continue;
    }
    if (role === 'overview') {
      if (line.trim()) doc.overview.push(line.trim());
      continue;
    }
    if (role === 'extras') {
      const last = doc.extras[doc.extras.length - 1];
      if (line.trim()) last.paragraphs.push(line.trim().replace(/^-\s+/, ''));
      continue;
    }

    const bullet = /^-\s+(.*)$/.exec(line);
    const indented = /^\s{2,}(.*)$/.exec(line);
    if (bullet) {
      const body = bullet[1].trim();
      const audit = /^条件核对\s+\*\*(.+?)\*\*：(.+)$/.exec(body);
      if (audit) {
        const detail = audit[2].replace(/[（(]([sf]\d+(?:\s*[,，]\s*[sf]\d+)*)[）)]\s*。?\s*$/, '').trim();
        const ids = /[（(]([sf]\d+(?:\s*[,，]\s*[sf]\d+)*)[）)]\s*。?\s*$/.exec(audit[2]);
        doc.audits.push({ topic: audit[1].trim(), detail, sources: ids ? ids[1].split(/[,，]\s*/) : [] });
        continue;
      }
      const labeled = /^\*\*(.+?)\*\*：([\s\S]*)$/.exec(body);
      if (labeled) {
        current = { label: labeled[1].trim(), text: labeled[2].trim(), conditions: [], sources: [] };
        doc.claims.push(current);
        continue;
      }
      if (role === 'summary') doc.summary.push(body);
      else doc.supplements.push(body);
      current = null;
      continue;
    }
    if (indented && current) {
      const value = indented[1].trim();
      const condition = /^条件：(.*)$/.exec(value);
      const source = /^出处：(.*)$/.exec(value);
      if (condition) current.conditions = condition[1].split('；').map((s) => s.trim()).filter(Boolean);
      else if (source) current.sources = source[1].split(/[,，]\s*/).map((s) => s.trim()).filter(Boolean);
    }
  }
  return doc;
}

function claimGroupKey(label) {
  if (label.startsWith('原作者')) return '原作者陈述';
  if (label.startsWith('整理者')) return '整理者分析';
  return label;
}

/* 出处编号 -> 可点的位置（B 站时间点或截图）。 */
function citeList(ids, locations, limit = 5) {
  const box = el('div', undefined, 'cites');
  box.append(el('span', '出处', 'cite-label'));
  for (const id of ids.slice(0, limit)) {
    const spot = locations[id];
    if (!spot) box.append(el('code', id, 'cite-missing'));
    else if (spot.href.startsWith('frames/')) box.append(frameButton(spot.href, spot.label, id));
    else box.append(externalLink(spot.label, spot.href, 'cite'));
  }
  if (ids.length > limit) box.append(el('span', `等 ${ids.length} 处`, 'cite-more'));
  return box;
}

function frameButton(href, text, id) {
  const name = href.split('/').pop();
  const wrap = el('span', undefined, 'frame-cite');
  const node = button(text, load, 'cite');
  node.title = id + '：点开看截图';
  let image = null;
  wrap.append(node);

  async function load() {
    if (image) {
      image.remove();
      image = null;
      return;
    }
    if (!state || !state.formal) {
      notice('还没有打开任何一份资料，刷新页面再试。', 'error');
      return;
    }
    node.disabled = true;
    try {
      const response = await fetch(`/api/frame/${name}?entry=${encodeURIComponent(state.formal.id)}`, { headers: { Authorization: 'Bearer ' + key } });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw fail((data && data.error) || '这张截图读不出来，可以刷新页面重试。', response.status);
      }
      const url = URL.createObjectURL(await response.blob());
      image = el('img', undefined, 'frame-shot');
      image.alt = id + ' 时刻的画面';
      image.src = url;
      image.onclick = () => { URL.revokeObjectURL(url); image.remove(); image = null; };
      (wrap.parentElement || wrap).append(image);
    } catch (error) {
      notice(error.message, 'error');
    } finally {
      node.disabled = false;
    }
  }
  return wrap;
}

function conditionLine(claim) {
  if (!claim.conditions.length) return null;
  const box = el('div', undefined, 'conditions-box');
  box.append(el('span', '只在这些前提下成立', 'condition-label'));
  const list = el('ul', undefined, 'conditions');
  for (const condition of claim.conditions) list.append(el('li', condition));
  box.append(list);
  return box;
}

function claimCard(claim, locations) {
  const group = claimGroupKey(claim.label);
  const card = el('div', undefined, 'claim');
  card.append(el('span', CLAIM_GROUP[group] ? CLAIM_GROUP[group][0] : claim.label, 'badge soft'));
  card.append(el('p', claim.text, 'claim-text'));
  const conditions = conditionLine(claim);
  if (conditions) card.append(conditions);
  if (claim.sources.length) card.append(citeList(claim.sources, locations));
  return card;
}

function fold(title, count, className) {
  const node = el('details', undefined, 'fold ' + (className || ''));
  node.append(el('summary', count === null ? title : `${title} · ${count} 条`));
  return node;
}

/* 把一份笔记渲染成：概览 -> 局限 -> 分段摘要 -> 关键条件 -> 详细笔记 -> 简介补充 -> 出处索引 */
function markdown(text) {
  const doc = parseNote(text);
  const box = el('div', undefined, 'reading');

  for (const paragraph of doc.overview) box.append(el('p', paragraph, 'lead'));

  const limits = doc.claims.filter((claim) => claimGroupKey(claim.label) === '来源局限');
  if (limits.length) {
    const block = el('section', undefined, 'callout warn');
    block.append(el('h3', CLAIM_GROUP['来源局限'][0]));
    block.append(el('p', CLAIM_GROUP['来源局限'][1], 'callout-hint'));
    const list = el('ul', undefined, 'limits');
    for (const claim of limits.slice(0, 3)) list.append(el('li', claim.text));
    block.append(list);
    if (limits.length > 3) {
      const rest = el('ul', undefined, 'limits');
      rest.hidden = true;
      for (const claim of limits.slice(3)) rest.append(el('li', claim.text));
      block.append(rest);
      const expand = button(`展开其余 ${limits.length - 3} 条`, () => {
        rest.hidden = false;
        expand.remove();
      }, 'link');
      block.append(expand);
    }
    box.append(block);
  }

  if (doc.summary.length) {
    const node = fold('分段摘要', doc.summary.length, 'summary-fold');
    node.open = true;
    const list = el('ol', undefined, 'summary-list');
    for (const item of doc.summary) list.append(el('li', item.replace(/^第\s*\d+\s*段：/, '')));
    node.append(list);
    box.append(node);
  }

  const claims = doc.claims.filter((claim) => claimGroupKey(claim.label) !== '来源局限');
  if (claims.length) {
    const node = fold('逐条笔记', claims.length, 'claim-fold');
    const groups = new Map();
    for (const claim of claims) {
      const group = claimGroupKey(claim.label);
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(claim);
    }
    const order = ['原作者陈述', '整理者分析'];
    for (const group of [...groups.keys()].sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))) {
      const block = el('section', undefined, 'claim-group');
      block.append(el('h4', CLAIM_GROUP[group] ? CLAIM_GROUP[group][0] : group));
      if (CLAIM_GROUP[group]) block.append(el('p', CLAIM_GROUP[group][1], 'group-hint'));
      for (const claim of groups.get(group)) block.append(claimCard(claim, doc.locations));
      node.append(block);
    }
    box.append(node);
  }

  if (doc.audits.length) {
    const node = fold('视频里点到的适用条件', doc.audits.length, 'audit-fold');
    node.append(el('p', '这些是视频里明确说过的前提。离开它们，下面的结论不一定成立。', 'group-hint'));
    for (const audit of doc.audits) {
      const row = el('div', undefined, 'audit');
      row.append(el('span', audit.topic, 'badge soft'));
      row.append(el('p', audit.detail));
      if (audit.sources.length) row.append(citeList(audit.sources, doc.locations));
      node.append(row);
    }
    box.append(node);
  }

  if (doc.supplements.length) {
    const node = fold('作者简介里的补充条件', doc.supplements.length, 'extra-fold');
    node.append(el('p', '引自同一视频的作者简介，是补充条件，不是画面转写。', 'group-hint'));
    const list = el('ul', undefined, 'limits');
    for (const item of doc.supplements) list.append(el('li', item));
    node.append(list);
    box.append(node);
  }

  for (const extra of doc.extras) {
    const node = fold(extra.title, null, 'extra-fold');
    for (const paragraph of extra.paragraphs) node.append(el('p', paragraph));
    box.append(node);
  }

  const spots = Object.entries(doc.locations);
  if (spots.length) {
    const node = fold('原文出处对照', spots.length, 'location-fold');
    node.append(el('p', '每个编号对应视频里的一个时间点或一张截图，正文里的出处编号都能在这里查到。', 'group-hint'));
    const grid = el('div', undefined, 'location-grid');
    for (const [id, spot] of spots) {
      const cell = spot.href.startsWith('frames/') ? frameButton(spot.href, id, id) : externalLink(id, spot.href, 'cite');
      const pair = el('div', undefined, 'location-item');
      pair.append(cell, el('span', spot.label, 'meta'));
      grid.append(pair);
    }
    node.append(grid);
    box.append(node);
  }
  return box;
}

/* ----------------------------- 渲染 ----------------------------- */

function render() {
  if (!state) return;
  if (state.library && state.library.length) {
    state.formal = state.library.find((item) => item.id === selectedEntry) || state.library[0];
    selectedEntry = state.formal.id;
  }
  document.querySelectorAll('nav button').forEach((node) => {
    const active = node.dataset.view === view;
    node.classList.toggle('active', active);
    if (active) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  });
  $('#heading').textContent = VIEW_TITLE[view];
  $('#subheading').textContent = contextLine();
  $('#pending-count').textContent = String(pendingCount());

  const content = $('#content');
  content.replaceChildren();
  if (view === 'pending') return renderPending(content);
  const hasLibrary = state.library && state.library.length;
  if (!hasLibrary) return renderEmpty(content);
  if (view === 'map') return content.append(createGraph());
  if (view === 'shelf') return renderShelf(content);
  renderReading(content);
}

function renderEmpty(content) {
  const section = el('section', undefined, 'empty');
  section.append(el('h2', '还没有正式入库的资料'));
  section.append(el('p', '资料要先通过完整性检查，才会出现在百科、书架和地图里。没通过之前，它只在「待处理」里等着。'));
  const actions = el('div', undefined, 'actions');
  if (pendingCount()) actions.append(button('看待处理', () => { view = 'pending'; render(); }));
  actions.append(button('去粘贴链接', () => $('#source').focus()));
  section.append(actions);
  content.append(section);
}

function renderShelf(content) {
  const grid = el('div', undefined, 'shelf-grid');
  for (const item of state.library) {
    const card = el('section', undefined, 'card');
    card.append(el('div', sourceMeta(item), 'meta'));
    card.append(el('h2', item.title));
    card.append(el('p', overviewOf(item.notes), 'excerpt'));
    card.append(button('阅读笔记', () => { selectedEntry = item.id; view = 'encyclopedia'; render(); }));
    grid.append(card);
  }
  content.append(grid);
}

function renderReading(content) {
  if (state.library.length > 1) {
    const row = el('div', undefined, 'picker-row');
    row.append(el('label', '正在看', 'picker-label'));
    const picker = el('select', undefined, 'source-picker');
    picker.setAttribute('aria-label', '选择资料');
    for (const item of state.library) {
      const option = el('option', item.title);
      option.value = item.id;
      option.selected = item.id === selectedEntry;
      picker.append(option);
    }
    picker.onchange = () => { selectedEntry = picker.value; render(); };
    row.append(picker);
    content.append(row);
  }
  const item = state.formal;
  const card = el('section', undefined, 'card note');
  card.append(el('div', sourceMeta(item), 'meta'));
  card.append(el('h2', item.title));
  card.append(externalLink('在 B 站看原视频 ↗', item.url));
  card.append(markdown(item.notes));
  content.append(card);
}

function renderPending(content) {
  for (const item of state.queued || []) {
    if ((state.jobs || []).some((job) => job.source === item.bvid)) continue;
    const stopped = String(item.status || '').includes('paused');
    const card = el('section', undefined, 'card');
    card.append(el('span', stopped ? '停在半路' : (JOB_STATUS[item.status]?.[0] || '正在处理'), 'badge ' + (stopped ? 'paused' : 'busy')));
    card.append(el('h2', item.title || item.bvid));
    card.append(el('p', (item.reasons || []).join('；') || '正在获取素材、转写语音或准备画面，完成后会更新分析进度。'));
    card.append(el('p', stopped
      ? '已取到的材料都留在本地，没有丢。按上面的原因处理后，可以重新提交同一条链接。'
      : '处理完成后会自动出现在百科里，不用一直守着。', 'meta'));
    if (stopped) {
      const retryButton = button('重新导入', async () => {
        retryButton.disabled = true;
        try {
          const result = await api('/api/submit', { url: item.bvid, allow_cloud: $('#cloud-import').checked });
          notice(result.message, 'ok');
          await refresh({ force: true });
        } catch (error) {
          notice(error.message, 'error');
        } finally {
          retryButton.disabled = false;
        }
      }, 'primary');
      card.append(retryButton);
    }
    content.append(card);
  }

  const jobs = state.jobs || [];
  for (const job of jobs) content.append(jobCard(job));

  if (!jobs.length && !(state.queued || []).length) {
    const section = el('section', undefined, 'empty');
    section.append(el('h2', '没有待处理的任务'));
    section.append(el('p', state.formal
      ? '已经入库的资料都在百科和书架里。想再加一条，回到顶部粘贴链接。'
      : '还没有开始整理任何视频。在顶部粘贴一条 B 站视频链接就能开始。'));
    const actions = el('div', undefined, 'actions');
    if (state.formal) actions.append(button('回到百科', () => { view = 'encyclopedia'; render(); }));
    actions.append(button('去粘贴链接', () => $('#source').focus()));
    section.append(actions);
    content.append(section);
  }
}

function jobCard(job) {
  const card = el('section', undefined, 'card');
  const [label, tone] = JOB_STATUS[job.status] || [job.status, 'busy'];
  card.append(el('span', label, 'badge ' + tone));
  card.append(el('h2', job.title || job.source));
  if (job.url) card.append(externalLink('在 B 站看原视频 ↗', job.url));

  if (job.reason) {
    card.append(el('p', '原因：' + job.reason, 'reason'));
    if (job.status === 'paused') card.append(el('p', '处理已经停下，旧记录和已用额度都保留着。想再试一次，勾选下面的选项再点按钮。', 'meta'));
  }

  const stats = el('div', undefined, 'stats');
  for (const [value, text] of [[`${job.completed}/${job.batches}`, '已分析段落'], [job.frames, '截图张数'], [`${job.attempts}`, '模型调用']]) {
    const cell = el('div');
    cell.append(el('strong', String(value)), document.createTextNode(text));
    stats.append(cell);
  }
  card.append(stats);

  const bar = el('progress');
  bar.max = Math.max(job.batches, 1);
  bar.value = job.completed;
  bar.setAttribute('aria-label', '分析进度');
  card.append(bar);

  const checks = el('div', undefined, 'checks');
  const consent = el('input');
  consent.type = 'checkbox';
  consent.checked = true;
  const consentLabel = el('label');
  consentLabel.append(consent, document.createTextNode('同意把这条视频的截图和画面文字交给本机已登录的 Codex 服务'));
  checks.append(consentLabel);

  let retry = null;
  if (job.status === 'paused') {
    retry = el('input');
    retry.type = 'checkbox';
    retry.checked = true;
    const retryLabel = el('label');
    retryLabel.append(retry, document.createTextNode('重新尝试（旧记录和已用额度不清零）'));
    checks.append(retryLabel);
  }
  card.append(checks);

  const action = button(JOB_ACTION[job.status] || '开始 / 继续分析', async () => {
    if (!consent.checked) {
      notice('先勾选同意，才能把截图和画面文字交给 Codex 分析。', 'error');
      return;
    }
    if (retry && !retry.checked) {
      notice('这是暂停过的任务。要重新开始，请勾选「重新尝试」。', 'error');
      return;
    }
    try {
      const result = await api('/api/analyze', { job: job.id, allow_cloud: consent.checked, explicit_retry: retry ? retry.checked : false });
      notice(result.message, 'ok');
      await refresh({ force: true });
    } catch (error) {
      notice(error.message, 'error');
    }
  }, 'primary');
  action.disabled = job.status === 'analyzing';
  card.append(action);
  return card;
}

/* ----------------------------- 知识地图 ----------------------------- */

function createGraph() {
  const root = el('section', undefined, 'graph-shell');
  const toolbar = el('form', undefined, 'graph-toolbar');
  const search = el('input');
  search.type = 'search';
  search.placeholder = '搜一个词，比如 BFS、退路';
  search.setAttribute('aria-label', '搜全部知识点');
  const submit = el('button', '搜索');
  submit.type = 'submit';
  const controls = el('div', undefined, 'graph-controls');
  toolbar.append(search, submit, controls);

  const filter = el('div', undefined, 'graph-filter');
  const status = el('span', '正在加载本地知识图谱…', 'graph-status');
  status.setAttribute('role', 'status');
  const density = el('label');
  density.append(document.createTextNode('连线密度 '));
  const threshold = el('select');
  threshold.setAttribute('aria-label', '连线密度');
  for (const [value, text] of [['0.55', '宽松（线多）'], ['0.65', '适中'], ['0.75', '严格（只连强的）']]) {
    const option = el('option', text);
    option.value = value;
    option.selected = value === '0.65';
    threshold.append(option);
  }
  density.append(threshold);

  const legend = el('span', undefined, 'graph-legend');
  for (const [cls, text] of [['source', '同属一份资料'], ['relation', '笔记里写明的关系'], ['similar', '只是语义相近']]) {
    legend.append(el('i', undefined, 'key-line ' + cls), el('span', text));
  }

  filter.append(status, density, legend);
  const domains = {algorithms:['算法与编程','#268078'], ai:['AI 模型与工具','#4778c5'], agents:['Agent 系统','#8959b5'], society:['社会与公共议题','#c28132'], other:['未分类','#77838d']};
  const domainLegend = el('div', undefined, 'domain-legend');
  for (const [key, [label, color]] of Object.entries(domains)) {
    const item = el('span', '● ' + label);
    item.style.color = color;
    domainLegend.append(item);
  }
  domainLegend.append(el('span', '◆ 资料　● 概念　■ 工具/产品　⬡ 算法/方法　◎ 跨领域'));
  filter.append(domainLegend);

  const stage = el('div', undefined, 'graph-stage');
  const ns = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const canvas = svg('svg', { viewBox: '0 0 1000 650', role: 'group', 'aria-label': '跨资料知识图谱' });
  const world = svg('g');
  canvas.append(world);
  stage.append(canvas);

  const panel = el('div', undefined, 'graph-detail');
  panel.setAttribute('aria-live', 'polite');
  const more = button('加载更多知识点', () => load(true), 'secondary load-more');
  root.append(toolbar, filter, stage, more, panel);

  let nodes = [];
  let edges = [];
  let zoom = 1;
  let tx = 0;
  let ty = 0;
  let selected = null;
  let hover = null;
  let drag = null;
  let offset = 0;
  let query = '';
  let focus = '';
  let serial = 0;

  function openEvidence(evidence) {
    selectedEntry = evidence.entryId;
    view = 'encyclopedia';
    render();
    if (!evidence.text) return;
    const paragraphs = [...document.querySelectorAll('.reading p')];
    let match = paragraphs.find((p) => p.textContent.includes(evidence.text));
    if (!match && evidence.text.length > 16) match = paragraphs.find((p) => p.textContent.includes(evidence.text.slice(0, 16)));
    if (!match) {
      notice('这段原文在笔记里没找到（可能资料已更新）。可以打开笔记自己搜一下。', 'error');
      return;
    }
    const fold = match.closest('details');
    if (fold) fold.open = true;
    match.classList.add('linked-passage');
    match.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function openNode(node) {
    openEvidence((node.evidence && node.evidence[0]) || { entryId: node.entryId });
  }

  function kindLabel(node) {
    if (node.kind === 'source') return '来源';
    return CONCEPT_KIND[node.claimType] || node.claimType || '知识点';
  }

  /* 词条里的「条件／出处」是给机器写的，这里只留条件，出处交给下面的按钮。 */
  function conditionOf(details) {
    const match = /条件：([\s\S]*?)(?:\n\s*出处：|$)/.exec(details || '');
    return match ? match[1].trim() : '';
  }

  function evidenceList(evidence) {
    const list = el('details', undefined, 'concept-evidence');
    list.append(el('summary', `原文出处 · ${evidence.length} 处`));
    for (const entry of evidence) {
      const item = el('div', undefined, 'evidence-item');
      const group = entry.kind && entry.kind.startsWith('原作者') ? '原作者陈述' : '整理者分析';
      item.append(el('span', CLAIM_GROUP[group][0], 'badge soft'));
      item.append(el('p', entry.text));
      const condition = conditionOf(entry.details);
      if (condition) item.append(el('p', '条件：' + condition, 'meta'));
      item.append(button('在笔记中定位 ↗', () => openEvidence(entry), 'link'));
      list.append(item);
    }
    return list;
  }

  function select(node) {
    selected = node.id;
    panel.replaceChildren();
    const info = el('div', undefined, 'concept');
    info.append(el('span', kindLabel(node), 'badge'));
    info.append(el('h2', node.label));
    if (node.categoryLabels) info.append(el('p', node.categoryLabels.join(' · '), 'meta'));
    if (node.kind === 'concept') {
      info.append(el('p', node.excerpt, 'definition'));
      info.append(el('p', node.synthesisStatus === 'synthesized' ? '综合词条 · 随资料更新' : '各来源定义汇总 · 尚未综合改写', 'meta'));
      for (const [key, title] of [['conditions', '适用条件'], ['differences', '补充与分歧']]) {
        if (node[key] && node[key].length) {
          info.append(el('h3', title));
          for (const text of node[key]) info.append(el('p', text));
        }
      }
      if (node.contributions && node.contributions.length) {
        const history = el('details');
        history.append(el('summary', '各来源的定义与更新'));
        for (const contribution of node.contributions) {
          history.append(el('h4', contribution.sourceTitle));
          history.append(el('p', contribution.definition));
        }
        info.append(history);
      }
      if (node.aliases && node.aliases.length) info.append(el('p', '也叫：' + node.aliases.join('、'), 'meta'));
      if (node.sourceIds && node.sourceIds.length > 1) info.append(el('p', `出现在 ${node.sourceIds.length} 份资料里`, 'meta'));
      const related = edges.filter((edge) => edge.basis && (edge.source === node.id || edge.target === node.id));
      if (related.length) {
        const list = el('div', undefined, 'concept-relations');
        for (const edge of related) {
          const other = nodes.find((n) => n.id === (edge.source === node.id ? edge.target : edge.source));
          if (!other) continue;
          list.append(button(`${edge.kind} · ${other.label}`, () => showRelation(edge), 'secondary small'));
        }
        info.append(list);
      }
      if (node.limitations && node.limitations.length) {
        const limits = el('details');
        limits.append(el('summary', '这份资料没讲清楚的地方'));
        for (const limitation of node.limitations) limits.append(el('p', limitation));
        info.append(limits);
      }
      info.append(evidenceList(node.evidence || []));
    }
    const actions = el('div', undefined, 'actions');
    actions.append(button(node.kind === 'concept' ? '在笔记中定位' : '打开这份笔记', () => openNode(node)));
    if (node.kind === 'concept') {
      actions.append(button('看相关联的知识点', () => { focus = node.id; query = ''; search.value = ''; load(false); }, 'secondary'));
    }
    panel.append(info, actions);
    draw();
  }

  function showRelation(edge) {
    panel.replaceChildren();
    const from = nodes.find((n) => n.id === edge.source);
    const to = nodes.find((n) => n.id === edge.target);
    const info = el('div', undefined, 'concept');
    info.append(el('span', edge.basis, 'badge soft'));
    info.append(el('h2', `${from ? from.label : '未知'} → ${edge.kind} → ${to ? to.label : '未知'}`));
    if (edge.explanation) info.append(el('p', edge.explanation, 'definition'));
    info.append(evidenceList(edge.evidence || []));
    panel.append(info);
    if (to) {
      const actions = el('div', undefined, 'actions');
      actions.append(button('看目标知识点', () => select(to), 'secondary'));
      panel.append(actions);
    }
    draw();
  }

  function layout() {
    const sourceNodes = nodes.filter((node) => node.kind === 'source');
    sourceNodes.forEach((node, index) => {
      node.x = 250 + index * 500 / Math.max(1, sourceNodes.length - 1);
      node.y = 320;
    });
    nodes.filter((node) => node.kind !== 'source').forEach((node, index) => {
      if (node.x !== undefined) return;
      const parent = sourceNodes.find((source) => source.id === node.sourceId) || { x: 500, y: 320 };
      const angle = index * 2.39996;
      node.x = parent.x + 130 * Math.cos(angle);
      node.y = parent.y + 150 * Math.sin(angle);
    });
    const byId = new Map(nodes.map((node) => [node.id, node]));
    for (let step = 0; step < 140; step++) {
      const force = nodes.map(() => ({ x: 0, y: 0 }));
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          let dx = nodes[i].x - nodes[j].x;
          let dy = nodes[i].y - nodes[j].y;
          const distance = Math.max(15, Math.hypot(dx, dy));
          if (!dx && !dy) dx = 1;
          const strength = 5000 / (distance * distance);
          force[i].x += dx / distance * strength;
          force[i].y += dy / distance * strength;
          force[j].x -= dx / distance * strength;
          force[j].y -= dy / distance * strength;
        }
      }
      for (const edge of edges) {
        const a = byId.get(edge.source);
        const b = byId.get(edge.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const pull = (distance - (edge.kind === '来源' ? 160 : 210)) * .008;
        const ai = nodes.indexOf(a);
        const bi = nodes.indexOf(b);
        force[ai].x += dx / distance * pull;
        force[ai].y += dy / distance * pull;
        force[bi].x -= dx / distance * pull;
        force[bi].y -= dy / distance * pull;
      }
      nodes.forEach((node, index) => {
        if (node.kind === 'source') return;
        node.x += force[index].x * 3;
        node.y += force[index].y * 3;
      });
    }
  }

  function rebuild() {
    world.replaceChildren();
    const ids = new Set(nodes.map((node) => node.id));
    edges = edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
    for (const edge of edges) {
      edge.line = svg('line', { class: 'graph-edge' + (edge.kind === '语义相近' ? ' semantic' : edge.basis ? ' explained' : '') });
      const title = svg('title');
      title.textContent = edge.kind + (edge.explanation ? '：' + edge.explanation : '') + (edge.score ? ' · ' + edge.score : '');
      edge.line.append(title);
      if (edge.basis) {
        edge.line.setAttribute('tabindex', '0');
        edge.line.setAttribute('role', 'button');
        edge.line.setAttribute('aria-label', '关系：' + edge.kind);
        edge.line.addEventListener('click', () => showRelation(edge));
        edge.line.addEventListener('keydown', (event) => { if (event.key === 'Enter') showRelation(edge); });
      }
      world.append(edge.line);
    }
    for (const node of nodes) {
      node.group = svg('g', { class: 'graph-node', tabindex: '0', role: 'button', 'aria-label': node.label });
      const categories = node.categories || ['other'];
      node.group.style.setProperty('--node-color', (domains[categories[0]] || domains.other)[1]);
      let circle;
      if (node.kind === 'source') circle = svg('path', {d:'M 0 -14 L 14 0 L 0 14 L -14 0 Z'});
      else if (/工具|产品|模型|平台|系统/.test(node.claimType || '')) circle = svg('rect', {x:-7,y:-7,width:14,height:14,rx:3});
      else if (/算法|方法|策略|机制/.test(node.claimType || '')) circle = svg('path', {d:'M -8 0 L -4 -7 L 4 -7 L 8 0 L 4 7 L -4 7 Z'});
      else circle = svg('circle', {r:7});
      circle.classList.add('node-shape');
      if (categories.length > 1) {
        const ring = svg('circle', {r:12, class:'domain-ring'});
        node.group.append(ring);
      }
      const text = svg('text', { y: 22, 'text-anchor': 'middle' });
      text.textContent = node.kind === 'source' ? node.label.slice(0, 15) + (node.label.length > 15 ? '…' : '') : node.label;
      const title = svg('title');
      title.textContent = node.excerpt;
      node.group.append(circle, text, title);
      world.append(node.group);
      node.group.addEventListener('pointerenter', () => { hover = node.id; draw(); });
      node.group.addEventListener('pointerleave', () => { hover = null; draw(); });
      node.group.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node); }
      });
      node.group.addEventListener('dblclick', () => openNode(node));
    }
    draw();
  }

  function draw() {
    world.setAttribute('transform', `translate(${tx} ${ty}) scale(${zoom})`);
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const active = hover || selected;
    for (const edge of edges) {
      const a = byId.get(edge.source);
      const b = byId.get(edge.target);
      if (!a || !b) continue;
      edge.line.setAttribute('x1', a.x);
      edge.line.setAttribute('y1', a.y);
      edge.line.setAttribute('x2', b.x);
      edge.line.setAttribute('y2', b.y);
      edge.line.classList.toggle('highlight', edge.source === active || edge.target === active);
    }
    for (const node of nodes) {
      if (!node.group) continue;
      node.group.setAttribute('transform', `translate(${node.x} ${node.y})`);
      node.group.classList.toggle('selected', node.id === selected);
      node.group.classList.toggle('source', node.kind === 'source');
    }
  }

  async function load(append = false) {
    const ticket = ++serial;
    more.disabled = true;
    submit.disabled = true;
    more.textContent = '加载更多知识点';
    status.textContent = append ? '正在加载更多…' : '正在用本地模型找相关知识点…';
    try {
      const nextOffset = append ? offset : 0;
      const params = new URLSearchParams({ q: query, focus, offset: nextOffset, limit: 24, threshold: threshold.value,
        include: append ? nodes.filter((node) => node.kind === 'concept').map((node) => node.id).join(',') : '' });
      const result = await api('/api/graph?' + params);
      if (ticket !== serial || !root.isConnected) return;
      if (!append) {
        nodes = [];
        edges = [];
        selected = null;
        panel.replaceChildren();
        zoom = 1;
        tx = ty = 0;
      }
      const known = new Set(nodes.map((node) => node.id));
      nodes.push(...result.nodes.filter((node) => !known.has(node.id)));
      const edgeIds = new Set(edges.map((edge) => edge.source + '|' + edge.target + '|' + edge.kind));
      edges.push(...result.edges.filter((edge) => !edgeIds.has(edge.source + '|' + edge.target + '|' + edge.kind)));
      offset = nextOffset + result.nodes.filter((node) => node.kind === 'concept').length;
      more.hidden = !result.hasMore;
      const concepts = nodes.filter((node) => node.kind === 'concept').length;
      status.textContent = result.total ? `${concepts} / ${result.total} 个知识点 · ${nodes.filter((node) => node.kind === 'source').length} 份资料` : '这份资料还没有知识点';
      layout();
      rebuild();
      if (!selected && nodes.length) select(nodes.find((node) => node.kind === 'concept') || nodes[0]);
    } catch (error) {
      if (ticket !== serial) return;
      status.textContent = '没加载出来：' + error.message;
      more.hidden = false;
      more.textContent = '重试';
    } finally {
      if (ticket === serial) {
        more.disabled = false;
        submit.disabled = false;
      }
    }
  }

  toolbar.onsubmit = (event) => {
    event.preventDefault();
    query = search.value.trim();
    focus = '';
    load(false);
  };
  threshold.onchange = () => load(false);

  function point(event) {
    const p = canvas.createSVGPoint();
    p.x = event.clientX;
    p.y = event.clientY;
    return p.matrixTransform(canvas.getScreenCTM().inverse());
  }

  function scale(factor, anchor = { x: 500, y: 325 }) {
    const next = Math.min(5, Math.max(.3, zoom * factor));
    tx = anchor.x - (anchor.x - tx) * next / zoom;
    ty = anchor.y - (anchor.y - ty) * next / zoom;
    zoom = next;
    draw();
  }

  controls.append(
    button('−', () => scale(.8), 'secondary'),
    button('＋', () => scale(1.25), 'secondary'),
    button('全部知识点', () => { query = focus = ''; search.value = ''; load(false); }, 'secondary'),
    button('居中', () => { zoom = 1; tx = ty = 0; draw(); }, 'secondary'),
  );
  controls.children[0].setAttribute('aria-label', '缩小图谱');
  controls.children[0].title = '缩小';
  controls.children[1].setAttribute('aria-label', '放大图谱');
  controls.children[1].title = '放大';

  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    scale(Math.exp(-event.deltaY * .001), point(event));
  }, { passive: false });
  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    const start = point(event);
    drag = { id: event.pointerId, point: start, start, node: nodes.find((node) => node.group.contains(event.target)), moved: false };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    const current = point(event);
    const dx = current.x - drag.point.x;
    const dy = current.y - drag.point.y;
    if (Math.hypot(current.x - drag.start.x, current.y - drag.start.y) > 4) drag.moved = true;
    if (drag.node) {
      drag.node.x += dx / zoom;
      drag.node.y += dy / zoom;
    } else {
      tx += dx;
      ty += dy;
    }
    drag.point = current;
    draw();
  });
  canvas.addEventListener('pointerup', (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    if (!drag.moved && drag.node) select(drag.node);
    drag = null;
    canvas.releasePointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointercancel', () => { drag = null; });

  queueMicrotask(() => load());
  return root;
}

/* ----------------------------- 生命周期 ----------------------------- */

function librarySignature(value) {
  return value && value.library ? value.library.map((item) => item.id).join('|') : '';
}

async function refresh(options = {}) {
  try {
    const next = await api('/api/state');
    const fingerprint = JSON.stringify(next);
    const changed = fingerprint !== lastSnapshot;
    const sameLibrary = librarySignature(next) === librarySignature(state);
    lastSnapshot = fingerprint;
    state = next;
    if (selectedEntry && state.library && state.library.length) {
      state.formal = state.library.find((item) => item.id === selectedEntry) || state.formal;
    }
    $('#login').hidden = true;
    $('#workspace').hidden = false;
    showLoginError('');
    sessionStorage.setItem('kb-key', key);
    if (options.force || changed) {
      // 正在拖着看地图时，后台进度变化不重建整张图，否则视野和选中会丢。
      if (view === 'map' && !options.force && sameLibrary) {
        $('#subheading').textContent = contextLine();
        $('#pending-count').textContent = String(pendingCount());
      } else {
        render();
      }
    }
  } catch (error) {
    if (error.status === 401) {
      key = '';
      sessionStorage.removeItem('kb-key');
      $('#login').hidden = false;
      $('#workspace').hidden = true;
      showLoginError(error.message);
      return;
    }
    notice(error.message, 'error');
  }
}

$('#login-form').onsubmit = (event) => {
  event.preventDefault();
  const value = $('#key').value.trim();
  if (!value) {
    showLoginError('先粘贴口令再进来。口令在启动窗口打印的地址里。');
    return;
  }
  key = value;
  refresh({ force: true });
};

$('#submit-form').onsubmit = async (event) => {
  event.preventDefault();
  const url = $('#source').value.trim();
  if (!url) {
    notice('先粘贴一条视频链接。', 'error');
    $('#source').focus();
    return;
  }
  try {
    const result = await api('/api/submit', { url, allow_cloud: $('#cloud-import').checked });
    state = result.state;
    // 已经开工或者有东西在排队，就去看进度；只是记下了链接，就留在原地，把选项面板展开。
    if (result.status === 202 || pendingCount()) view = 'pending';
    if (/导入选项/.test(result.message)) {
      const options = document.querySelector('.import-options');
      if (options) options.open = true;
    }
    notice(result.message, 'ok');
    render();
  } catch (error) {
    notice(error.message, 'error');
  }
};

document.querySelectorAll('nav button').forEach((node) => {
  node.onclick = () => {
    if (view === node.dataset.view) return;
    view = node.dataset.view;
    render();
  };
});

window.addEventListener('hashchange', () => {
  const next = new URLSearchParams(location.hash.slice(1)).get('key');
  if (!next) return;
  key = next;
  history.replaceState(null, '', location.pathname);
  refresh({ force: true });
});

if (key) refresh();
setInterval(() => { if (key) refresh(); }, 6000);

/* 供 Node 环境下的解析校验使用（浏览器里等同挂一个全局变量）。 */
if (typeof window !== 'undefined') window.__kb = { parseNote, markdown };
