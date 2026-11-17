'use strict';
// Poll loop and "what's new" bookkeeping, independent of Electron.
const { sortPages } = require('./pages');

class PagerCore {
  constructor({ sources = [], seen = [], onChange = () => {}, onNew = () => {} } = {}) {
    this.sources = sources;
    this.seen = new Set(seen);
    this.pages = [];
    this.errors = [];
    this.lastPoll = null;
    this.onChange = onChange;
    this.onNew = onNew;
    this.firstPoll = seen.length === 0;
    this.authError = false;
    // ids answered here recently: a poll that raced the close must not resurrect them
    this.answered = new Map();
  }

  async poll() {
    const results = await Promise.allSettled(this.sources.map((s) => s.poll()));
    const pages = [];
    const errors = [];
    let authError = false;
    results.forEach((r, i) => {
      if (r.status === 'rejected' && r.reason && r.reason.constructor && r.reason.constructor.name === 'AuthError') authError = true;
      if (r.status === 'fulfilled') pages.push(...r.value.map((p) => ({ ...p, _src: i })));
      else errors.push(String(r.reason && r.reason.message ? r.reason.message : r.reason));
    });
    // If a source failed, keep its previous pages rather than flashing empty.
    for (let i = 0; i < results.length; i++) {
      if (results[i].status === 'rejected') pages.push(...this.pages.filter((p) => p._src === i));
    }
    const now = Date.now();
    for (const [id, t] of this.answered) if (now - t > 10 * 60 * 1000) this.answered.delete(id);
    const live = pages.filter((p) => !this.answered.has(p.id));
    pages.length = 0;
    pages.push(...live);
    const fresh = pages.filter((p) => !this.seen.has(p.id));
    fresh.forEach((p) => this.seen.add(p.id));
    // Don't fire a burst of notifications for a backlog on the very first run.
    if (fresh.length && !this.firstPoll) this.onNew(sortPages(fresh));
    this.firstPoll = false;
    this.pages = sortPages(pages);
    this.errors = errors;
    this.authError = authError;
    this.lastPoll = new Date().toISOString();
    this.onChange(this.snapshot());
    return this.pages;
  }

  async answer(id, text, opts) {
    const page = this.pages.find((p) => p.id === id);
    if (!page) throw new Error('That page is gone (already answered elsewhere?)');
    if (this.answered.has(id)) return; // double click
    this.answered.set(id, Date.now());
    try {
      await this.sources[page._src].answer(page, text, opts);
    } catch (e) {
      this.answered.delete(id);
      throw e;
    }
    this.pages = this.pages.filter((p) => p.id !== id);
    this.onChange(this.snapshot());
  }

  snapshot() {
    return {
      pages: this.pages.map(({ _src, ...p }) => p),
      errors: this.errors,
      authError: this.authError,
      lastPoll: this.lastPoll,
    };
  }

  // Keep the seen list bounded; only ids still open or recent matter.
  seenList(max = 500) {
    return [...this.seen].slice(-max);
  }
}

module.exports = { PagerCore };
