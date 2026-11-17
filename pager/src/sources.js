'use strict';
// Where pages come from. Both sources take an injectable fetch so tests can
// run without network or Electron.
const { fromGithubIssue, fromNtfy, formatAnswer } = require('./pages');

const UA = 'mos-pager/0.1';
const TIMEOUT_MS = 15000;

class AuthError extends Error {}

class GithubSource {
  // authors: whose issues count as pages. Default: only the token's owner,
  // because agents open issues as you through the GitHub connector, and an
  // issue from anyone else (a public repo, an org teammate) could be a fake
  // page. Use ["*"] to accept everyone.
  constructor({ token, label = 'needs-joon', repos = [], authors = [], api = 'https://api.github.com', fetchImpl = fetch }) {
    this.token = token;
    this.label = label;
    this.repos = repos.map((r) => String(r).toLowerCase());
    this.authors = authors.map((a) => String(a).toLowerCase());
    this.me = null;
    this.api = api.replace(/\/$/, '');
    this.fetch = fetchImpl;
    this.etag = null;
    this.cache = [];
  }

  headers(extra = {}) {
    return {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${this.token}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
      ...extra,
    };
  }

  // /issues?filter=all covers every repo the token can see (owned, collab,
  // org), so pages from any project show up without per-repo config.
  // ETag polling: a 304 doesn't count against the rate limit.
  async req(url, opts = {}) {
    return this.fetch(url, { ...opts, signal: AbortSignal.timeout(TIMEOUT_MS) });
  }

  async whoami() {
    if (this.me) return this.me;
    const r = await this.req(`${this.api}/user`, { headers: this.headers() });
    if (r.status === 401) throw new AuthError('GitHub token rejected (401). Run `gh auth login` again or paste a new token.');
    if (!r.ok) throw new Error(`GitHub /user ${r.status}`);
    this.me = String((await r.json()).login || '').toLowerCase();
    return this.me;
  }

  async poll() {
    const me = await this.whoami();
    const allowed = this.authors.length ? this.authors : [me];
    const url = `${this.api}/issues?filter=all&state=open&labels=${encodeURIComponent(this.label)}&per_page=100&sort=created`;
    const res = await this.req(url, { headers: this.headers(this.etag ? { 'If-None-Match': this.etag } : {}) });
    if (res.status === 304) return this.cache;
    if (res.status === 401) throw new AuthError('GitHub token rejected (401). Run `gh auth login` again or paste a new token.');
    if (!res.ok) throw new Error(`GitHub ${res.status}: ${(await res.text()).slice(0, 200)}`);
    this.etag = res.headers.get('etag');
    const issues = await res.json();
    this.cache = issues
      .filter((i) => !i.pull_request)
      .map(fromGithubIssue)
      .filter((p) => !this.repos.length || this.repos.includes(p.repo.toLowerCase()))
      .filter((p) => allowed.includes('*') || allowed.includes(p.author.toLowerCase()));
    return this.cache;
  }

  async answer(page, text, { close = true } = {}) {
    const base = `${this.api}/repos/${page.repo}/issues/${page.number}`;
    if (text) {
      const r = await this.req(`${base}/comments`, {
        method: 'POST',
        headers: this.headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ body: formatAnswer(text) }),
      });
      if (!r.ok) throw new Error(`Could not comment (${r.status})`);
    }
    if (close) {
      const r = await this.req(base, {
        method: 'PATCH',
        headers: this.headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ state: 'closed', state_reason: text ? 'completed' : 'not_planned' }),
      });
      if (!r.ok) throw new Error(`Could not close (${r.status})`);
    }
    this.etag = null; // force a fresh list next poll
  }
}

// Optional: ntfy.sh (free) or a self-hosted ntfy, for local senders that
// don't have GitHub. The topic name is the shared secret, so make it long.
class NtfySource {
  constructor({ server = 'https://ntfy.sh', topic, token = '', fetchImpl = fetch, dismissed = new Set() }) {
    this.server = server.replace(/\/$/, '');
    this.topic = topic;
    this.token = token;
    this.fetch = fetchImpl;
    this.dismissed = dismissed;
  }

  headers() {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  async poll() {
    const res = await this.fetch(`${this.server}/${encodeURIComponent(this.topic)}/json?poll=1&since=24h`, { headers: this.headers(), signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!res.ok) throw new Error(`ntfy ${res.status}`);
    const lines = (await res.text()).split('\n').filter(Boolean);
    const pages = [];
    for (const line of lines) {
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        continue;
      }
      if (msg.event !== 'message' || this.dismissed.has(`ntfy:${msg.id}`)) continue;
      pages.push(fromNtfy(msg));
    }
    return pages;
  }

  async answer(page, text) {
    if (text) {
      const r = await this.fetch(`${this.server}/${encodeURIComponent(this.topic + '-answers')}`, {
        method: 'POST',
        headers: { ...this.headers(), Title: `answer:${page.ntfyId}` },
        signal: AbortSignal.timeout(TIMEOUT_MS),
        body: String(text),
      });
      if (!r.ok) throw new Error(`ntfy answer failed (${r.status})`);
    }
    this.dismissed.add(page.id);
  }
}

module.exports = { GithubSource, NtfySource, AuthError };
