'use strict';
/* global pager */
// All page text is untrusted (written by agents / anyone who can open an
// issue), so it only ever goes into textContent, never innerHTML.

const $ = (id) => document.getElementById(id);
const pet = $('pet');
let current = { pages: [], errors: [], paused: false };
const drafts = new Map(); // keep typed replies across re-renders
const busy = new Set(); // ids whose answer is being sent
let pointerInList = false;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(s)) return '';
  if (s < 90) return 'just now';
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 129600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

async function act(card, id, fn) {
  if (busy.has(id)) return;
  busy.add(id);
  card.classList.add('busy');
  try {
    await fn();
    if (document.activeElement) document.activeElement.blur();
    busy.delete(id);
    render(current);
  } catch (e) {
    busy.delete(id);
    card.classList.remove('busy');
    $('errors').textContent = String(e.message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '');
  }
}

function renderCard(p) {
  const card = el('li', `card ${p.severity}${busy.has(p.id) ? ' busy' : ''}`);
  const meta = el('div', 'meta');
  if (p.project) meta.append(el('span', 'chip', p.project));
  meta.append(el('span', 'sev', p.severity));
  meta.append(el('span', '', [p.repo && p.number ? `${p.repo}#${p.number}` : '', ago(p.createdAt)].filter(Boolean).join(' · ')));
  card.append(meta, el('h3', '', p.title));
  if (p.excerpt) card.append(el('p', 'text', p.excerpt));

  if (p.options.length) {
    const opts = el('div', 'options');
    p.options.forEach((o, i) => {
      const b = el('button', p.recommended === i + 1 ? 'rec' : '', o);
      b.title = 'Send this answer and close the page';
      b.onclick = () => act(card, p.id, () => pager.answer(p.id, o, true));
      opts.append(b);
    });
    card.append(opts);
  }

  const ta = el('textarea');
  ta.placeholder = p.options.length ? 'Or write your own answer…' : 'Write an answer…';
  ta.value = drafts.get(p.id) || '';
  ta.oninput = () => drafts.set(p.id, ta.value);
  ta.onkeydown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && ta.value.trim()) send.click();
  };
  card.append(ta);

  const links = el('div', 'links');
  const send = el('button', 'primary', 'Send');
  send.title = 'Post your answer (⌘/Ctrl+Enter) and close the page';
  send.onclick = () => {
    if (!ta.value.trim()) return ta.focus();
    act(card, p.id, async () => {
      await pager.answer(p.id, ta.value.trim(), true);
      drafts.delete(p.id);
    });
  };
  links.append(send);
  if (p.thread) {
    const b = el('button', '', 'Open thread');
    b.onclick = () => pager.open(p.thread);
    links.append(b);
  }
  if (p.url) {
    const b = el('button', '', 'Issue');
    b.onclick = () => pager.open(p.url);
    links.append(b);
  }
  const done = el('button', '', 'Dismiss');
  done.title = 'Close without answering (you will reply elsewhere)';
  done.onclick = () => act(card, p.id, () => pager.answer(p.id, '', true));
  links.append(done);
  card.append(links);
  return card;
}

function render(d) {
  current = d;
  const pages = d.pages || [];
  const top = pages[0] && pages[0].severity;
  document.body.classList.toggle('has-blocker', top === 'blocker');
  document.body.classList.toggle('has-decision', top === 'decision');
  document.body.classList.toggle('has-fyi', top === 'fyi');
  document.body.classList.toggle('paused', !!d.paused);

  const badge = pet.querySelector('.badge');
  badge.hidden = !pages.length;
  badge.textContent = pages.length > 9 ? '9+' : String(pages.length);
  $('count').textContent = String(pages.length);
  $('dnd').classList.toggle('on', !!d.paused);
  $('dnd').textContent = d.paused ? '🔕' : '🔔';
  $('dnd').title = d.paused ? 'Do not disturb is on (click to turn off)' : 'Turn on do not disturb';
  $('setup').hidden = !d.needsSetup;
  $('forgetToken').hidden = !d.hasSavedToken;
  $('errors').textContent = (d.errors || []).join('\n');
  $('empty').hidden = !!pages.length || d.needsSetup;

  // Don't rebuild under someone typing a reply or mid-click.
  const focused = document.activeElement && document.activeElement.tagName === 'TEXTAREA';
  if (!focused && !pointerInList) {
    const list = $('list');
    list.replaceChildren(...pages.map(renderCard));
  }
  $('status').textContent = d.lastPoll ? `Checked ${ago(d.lastPoll)}${d.tokenSource ? ` · GitHub via ${d.tokenSource}` : ''}${d.paused ? ' · do not disturb' : ''}` : 'Starting…';
}

$('list').addEventListener('pointerdown', () => { pointerInList = true; });
window.addEventListener('pointerup', () => { pointerInList = false; }, true);

// Drag the pet by hand; a press that barely moves is a click.
let drag = null;
pet.addEventListener('pointerdown', (e) => {
  if (e.button !== 0) return;
  drag = { moved: 0, pending: false, sx: e.screenX, sy: e.screenY };
  pet.setPointerCapture(e.pointerId);
  pet.style.cursor = 'grabbing';
  pager.dragStart();
});
pet.addEventListener('pointermove', async (e) => {
  if (!drag) return;
  // Renderer coords are only good enough to tell a drag from a click.
  drag.moved = Math.max(drag.moved, Math.abs(e.screenX - drag.sx) + Math.abs(e.screenY - drag.sy));
  if (drag.pending) return;
  drag.pending = true; // one IPC in flight at a time keeps it smooth
  const d = drag;
  const moved = await pager.dragMove();
  d.moved = Math.max(d.moved, moved || 0);
  d.pending = false;
});
pet.addEventListener('pointerup', () => {
  pet.style.cursor = '';
  if (drag && drag.moved < 5) pager.togglePanel();
  drag = null;
  pager.dragEnd();
});
pet.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') pager.togglePanel();
});

$('close').onclick = () => pager.collapse();
$('hide').onclick = () => pager.hide();
$('refresh').onclick = () => pager.refresh();
$('dnd').onclick = () => pager.setPaused(!current.paused);
$('saveToken').onclick = async () => {
  const v = $('token').value.trim();
  if (!v) return;
  try {
    await pager.saveToken(v);
    $('token').value = '';
  } catch (e) {
    $('errors').textContent = String(e.message || e);
  }
};
$('forgetToken').onclick = () => pager.forgetToken();
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') pager.collapse();
});

pager.onPages(render);
pager.onExpanded((on) => {
  document.body.classList.toggle('expanded', on);
  if (!on) render(current);
});
setInterval(() => render(current), 60000); // refresh "x min ago"
pager.ready();
