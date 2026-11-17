'use strict';
// Page format shared by every sender (GitHub issue body, ntfy message, CLI).
// Pure functions only, so they are unit-tested without Electron.
//
//   <!-- mos-pager
//   project: MOS
//   thread: https://claude.ai/code/project/...?thread=cmsg_...
//   severity: decision            (decision | blocker | fyi)
//   options: Option A | Option B   (optional, pipe-separated)
//   recommended: 1                 (optional, 1-based)
//   -->
//   Free markdown context...

const SEVERITIES = ['blocker', 'decision', 'fyi'];
const META_RE = /<!--\s*mos-pager\b([\s\S]*?)-->/i;
const ANSWER_MARK = '<!-- mos-pager-answer -->';

function normalizeSeverity(s) {
  s = String(s || '').trim().toLowerCase();
  if (SEVERITIES.includes(s)) return s;
  // Friendly aliases so a slightly-off sender still lands somewhere sensible.
  if (['high', 'urgent', 'critical', 'blocking'].includes(s)) return 'blocker';
  if (['low', 'info', 'note'].includes(s)) return 'fyi';
  return 'decision';
}

function parseMeta(text) {
  const src = String(text || '');
  const m = src.match(META_RE);
  const meta = {};
  if (m) {
    for (const line of m[1].split(/\r?\n/)) {
      const kv = line.match(/^\s*([A-Za-z_]+)\s*:\s*(.*?)\s*$/);
      if (kv) meta[kv[1].toLowerCase()] = kv[2];
    }
  }
  const rest = (m ? src.slice(0, m.index) + src.slice(m.index + m[0].length) : src).trim();
  const options = (meta.options || '')
    .split('|')
    .map((o) => o.trim())
    .filter(Boolean)
    .slice(0, 6);
  let recommended = parseInt(meta.recommended, 10);
  if (!(recommended >= 1 && recommended <= options.length)) recommended = 0;
  return {
    project: (meta.project || '').slice(0, 60),
    thread: safeUrl(meta.thread),
    severity: normalizeSeverity(meta.severity || meta.urgency),
    options,
    recommended,
    text: rest,
  };
}

// Only open web links we expect; never file:, javascript:, custom schemes,
// and (for links taken from page text) only hosts agents actually live on.
const LINK_HOSTS = ['claude.ai', 'github.com'];
function safeUrl(u, hosts = LINK_HOSTS) {
  if (!u) return '';
  try {
    const url = new URL(String(u).trim());
    if (url.protocol !== 'https:') return '';
    if (hosts && !hosts.some((h) => url.hostname === h || url.hostname.endsWith('.' + h))) return '';
    return url.toString();
  } catch {
    return '';
  }
}

// Markdown-ish text -> short plain excerpt for the card.
function excerpt(text, max = 320) {
  const plain = String(text || '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/```[\s\S]*?```/g, '[code]')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/[*_`>]/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return plain.length > max ? plain.slice(0, max - 1).trimEnd() + '…' : plain;
}

function cleanTitle(t) {
  return String(t || '').replace(/^\s*\[(page|needs-joon|pager)\]\s*/i, '').trim().slice(0, 200) || '(untitled)';
}

function fromGithubIssue(issue) {
  const meta = parseMeta(issue.body);
  const repo = issue.repository && issue.repository.full_name
    ? issue.repository.full_name
    : String(issue.repository_url || '').replace(/^.*\/repos\//, '');
  return {
    id: `gh:${repo}#${issue.number}`,
    source: 'github',
    repo,
    number: issue.number,
    url: safeUrl(issue.html_url),
    title: cleanTitle(issue.title),
    project: meta.project || repo.split('/').pop() || '',
    thread: meta.thread,
    severity: meta.severity,
    options: meta.options,
    recommended: meta.recommended,
    excerpt: excerpt(meta.text),
    author: (issue.user && issue.user.login) || '',
    createdAt: issue.created_at,
  };
}

function fromNtfy(msg) {
  const meta = parseMeta(msg.message);
  return {
    id: `ntfy:${msg.id}`,
    source: 'ntfy',
    topic: msg.topic,
    ntfyId: msg.id,
    url: '',
    title: cleanTitle(msg.title || meta.text.split('\n')[0]),
    project: meta.project || 'ntfy',
    thread: meta.thread || safeUrl(msg.click),
    author: '',
    severity: meta.severity,
    options: meta.options,
    recommended: meta.recommended,
    excerpt: excerpt(meta.text),
    createdAt: new Date((msg.time || 0) * 1000).toISOString(),
  };
}

function formatPage({ project = '', thread = '', severity = 'decision', options = [], recommended = 0, body = '' }) {
  const clean = (s) => String(s).replace(/-->/g, '- ->').replace(/[\r\n]+/g, ' ').trim();
  const lines = ['<!-- mos-pager'];
  if (project) lines.push(`project: ${clean(project)}`);
  if (thread) lines.push(`thread: ${clean(thread)}`);
  lines.push(`severity: ${normalizeSeverity(severity)}`);
  if (options.length) lines.push(`options: ${options.map((o) => clean(o).replace(/\|/g, '/')).join(' | ')}`);
  if (recommended) lines.push(`recommended: ${recommended}`);
  lines.push('-->');
  return lines.join('\n') + '\n' + String(body || '').trim() + '\n';
}

function formatAnswer(answer) {
  return `${ANSWER_MARK}\n**Answer:** ${String(answer).trim()}\n\n_Sent from MOS Pager._`;
}

const SEVERITY_RANK = { blocker: 0, decision: 1, fyi: 2 };
function sortPages(pages) {
  return [...pages].sort(
    (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || String(b.createdAt).localeCompare(String(a.createdAt)),
  );
}

module.exports = {
  LINK_HOSTS,
  ANSWER_MARK,
  SEVERITIES,
  parseMeta,
  safeUrl,
  excerpt,
  cleanTitle,
  fromGithubIssue,
  fromNtfy,
  formatPage,
  formatAnswer,
  sortPages,
  normalizeSeverity,
};
