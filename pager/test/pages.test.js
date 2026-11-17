'use strict';
const test = require('node:test');
const assert = require('node:assert');
const P = require('../src/pages');

test('round-trips formatPage -> parseMeta', () => {
  const body = P.formatPage({
    project: 'MOS', thread: 'https://claude.ai/code/project/x?thread=cmsg_1', severity: 'blocker',
    options: ['Keep 8B', 'Try 2B | quantized'], recommended: 2, body: '## Why\nGPU cost.',
  });
  const m = P.parseMeta(body);
  assert.equal(m.project, 'MOS');
  assert.equal(m.severity, 'blocker');
  assert.deepEqual(m.options, ['Keep 8B', 'Try 2B / quantized']);
  assert.equal(m.recommended, 2);
  assert.equal(m.thread, 'https://claude.ai/code/project/x?thread=cmsg_1');
  assert.equal(m.text, '## Why\nGPU cost.');
});

test('no header still yields a usable page', () => {
  const m = P.parseMeta('just a plain issue');
  assert.equal(m.severity, 'decision');
  assert.deepEqual(m.options, []);
  assert.equal(m.text, 'just a plain issue');
});

test('rejects non-https thread links and bad recommended', () => {
  const m = P.parseMeta('<!-- mos-pager\nthread: javascript:alert(1)\noptions: a | b\nrecommended: 7\n-->');
  assert.equal(m.thread, '');
  assert.equal(m.recommended, 0);
  assert.equal(P.safeUrl('file:///etc/passwd'), '');
  assert.equal(P.safeUrl('https://evil.example/claude.ai'), '');
  assert.equal(P.safeUrl('https://claude.ai.evil.example/'), '');
  assert.ok(P.safeUrl('https://claude.ai/code/x'));
});

test('severity aliases', () => {
  assert.equal(P.normalizeSeverity('URGENT'), 'blocker');
  assert.equal(P.normalizeSeverity('info'), 'fyi');
  assert.equal(P.normalizeSeverity('whatever'), 'decision');
});

test('formatPage cannot be broken out of the comment', () => {
  const body = P.formatPage({ project: 'x --> <img src=x>', body: 'b' });
  assert.equal(P.parseMeta(body).project, 'x - -> <img src=x>');
});

test('fromGithubIssue maps fields and strips title prefix', () => {
  const p = P.fromGithubIssue({
    number: 12, title: '[needs-joon] Which model?', html_url: 'https://github.com/o/r/issues/12',
    repository: { full_name: 'o/r' }, created_at: '2026-11-17T08:00:00Z',
    body: P.formatPage({ severity: 'fyi', body: 'Some **context** [link](https://x)' }),
  });
  assert.equal(p.id, 'gh:o/r#12');
  assert.equal(p.title, 'Which model?');
  assert.equal(p.project, 'r');
  assert.equal(p.severity, 'fyi');
  assert.equal(p.excerpt, 'Some context link');
});

test('sortPages puts blockers first, then newest', () => {
  const s = P.sortPages([
    { id: 'a', severity: 'fyi', createdAt: '3' },
    { id: 'b', severity: 'decision', createdAt: '1' },
    { id: 'c', severity: 'blocker', createdAt: '0' },
    { id: 'd', severity: 'decision', createdAt: '2' },
  ]);
  assert.deepEqual(s.map((p) => p.id), ['c', 'd', 'b', 'a']);
});
