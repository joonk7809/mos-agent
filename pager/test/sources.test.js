'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { GithubSource, NtfySource, AuthError } = require('../src/sources');
const { PagerCore } = require('../src/core');
const { formatPage, ANSWER_MARK } = require('../src/pages');

function res(status, body, headers = {}) {
  return {
    status, ok: status >= 200 && status < 300,
    headers: { get: (k) => headers[k.toLowerCase()] || null },
    json: async () => body, text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  };
}
const issue = (n, extra = {}) => ({
  number: n, title: `Q${n}`, html_url: `https://github.com/o/r/issues/${n}`,
  repository: { full_name: 'o/r' }, user: { login: 'Me' }, created_at: `2026-11-17T0${n}:00:00Z`, body: formatPage({ body: 'ctx' }), ...extra,
});

test('github: polls with label, skips PRs, uses ETag, answers and closes', async () => {
  const calls = [];
  const fetchImpl = async (url, opts = {}) => {
    calls.push({ url, ...opts });
    if (url.endsWith('/user')) return res(200, { login: 'me' });
    if (url.includes('/issues?')) {
      if (opts.headers['If-None-Match'] === 'W/"1"') return res(304, '');
      return res(200, [issue(1), issue(2, { pull_request: {} })], { etag: 'W/"1"' });
    }
    return res(url.endsWith('/comments') ? 201 : 200, {});
  };
  const s = new GithubSource({ token: 't', fetchImpl });
  const first = await s.poll();
  assert.equal(first.length, 1);
  assert.match(calls[1].url, /labels=needs-joon/);
  assert.equal(calls[1].headers.Authorization, 'Bearer t');
  const second = await s.poll();
  assert.deepEqual(second, first);
  await s.answer(first[0], 'Option A');
  const comment = calls.find((c) => c.url.endsWith('/issues/1/comments'));
  assert.ok(JSON.parse(comment.body).body.startsWith(ANSWER_MARK));
  const close = calls.find((c) => c.method === 'PATCH');
  assert.deepEqual(JSON.parse(close.body), { state: 'closed', state_reason: 'completed' });
});

test('github: repo filter, author filter, 401', async () => {
  const f = (list) => async (url) => (url.endsWith('/user') ? res(200, { login: 'me' }) : res(200, list));
  assert.equal((await new GithubSource({ token: 't', repos: ['x/y'], fetchImpl: f([issue(1)]) }).poll()).length, 0);
  const stranger = issue(2, { user: { login: 'mallory' } });
  assert.deepEqual((await new GithubSource({ token: 't', fetchImpl: f([issue(1), stranger]) }).poll()).map((p) => p.number), [1]);
  assert.equal((await new GithubSource({ token: 't', authors: ['*'], fetchImpl: f([issue(1), stranger]) }).poll()).length, 2);
  const bad = new GithubSource({ token: 't', fetchImpl: async () => res(401, 'nope') });
  await assert.rejects(bad.poll(), AuthError);
});

test('ntfy: parses stream, hides dismissed', async () => {
  const lines = [
    { id: 'm1', event: 'message', time: 1, topic: 't', title: 'Hello', message: formatPage({ severity: 'blocker', body: 'b' }) },
    { id: 'k', event: 'keepalive' },
    { id: 'm2', event: 'message', time: 2, topic: 't', message: 'plain' },
  ].map((o) => JSON.stringify(o)).join('\n');
  const posted = [];
  const fetchImpl = async (url, opts = {}) => {
    if (opts.method === 'POST') { posted.push({ url, ...opts }); return res(200, {}); }
    return res(200, lines);
  };
  const s = new NtfySource({ topic: 't', fetchImpl });
  const pages = await s.poll();
  assert.equal(pages.length, 2);
  assert.equal(pages[0].severity, 'blocker');
  await s.answer(pages[0], 'yes');
  assert.match(posted[0].url, /\/t-answers$/);
  assert.equal(posted[0].headers.Title, 'answer:m1');
  assert.equal((await s.poll()).length, 1);
});

test('core: notifies only on genuinely new pages, keeps pages when a source fails', async () => {
  let batch = [issue(1)];
  let fail = false;
  const src = { poll: async () => { if (fail) throw new Error('down'); return batch.map((i) => require('../src/pages').fromGithubIssue(i)); }, answer: async () => { answers++; } };
  let answers = 0;
  const fresh = [];
  const core = new PagerCore({ sources: [src], onNew: (p) => fresh.push(...p) });
  await core.poll();
  assert.equal(fresh.length, 0, 'backlog on first run is not a burst of notifications');
  batch = [issue(1), issue(2)];
  await core.poll();
  assert.deepEqual(fresh.map((p) => p.number), [2]);
  fail = true;
  await core.poll();
  assert.equal(core.pages.length, 2);
  assert.deepEqual(core.errors, ['down']);
  fail = false;
  await Promise.all([core.answer('gh:o/r#2', 'ok'), core.answer('gh:o/r#2', 'ok')]);
  assert.equal(answers, 1, 'double click sends once');
  await core.poll(); // a stale poll that still lists #2 must not bring it back
  assert.equal(core.snapshot().pages.length, 1);
  assert.ok(!('_src' in core.snapshot().pages[0]));
});
