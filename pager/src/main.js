'use strict';
const {
  app, BrowserWindow, Tray, Menu, Notification, nativeImage, ipcMain, screen, shell, globalShortcut, safeStorage,
} = require('electron');
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { PagerCore } = require('./core');
const { GithubSource, NtfySource } = require('./sources');
const { safeUrl } = require('./pages');

const PET = { w: 96, h: 96 };
const PANEL = { w: 380, h: 520 };
const DEFAULT_CONFIG = {
  label: 'needs-joon',
  repos: [],
  authors: [],
  pollSeconds: 30,
  shortcut: 'CommandOrControl+Alt+P',
  ntfy: { server: 'https://ntfy.sh', topic: '', token: '' },
};

if (!app.requestSingleInstanceLock()) app.quit();
if (process.platform === 'linux' && process.env.MOS_PAGER_NO_SANDBOX) app.commandLine.appendSwitch('no-sandbox');

let win = null;
let tray = null;
let core = null;
let timer = null;
let expanded = false;
let state = {};
let config = {};
let tokenSource = '';
let configError = '';
let tokenWarning = '';
let generation = 0;
let shortcutError = '';
const liveNotes = new Set();

const dataDir = () => app.getPath('userData');
const configPath = () => path.join(dataDir(), 'config.json');
const statePath = () => path.join(dataDir(), 'state.json');

function readJson(p, fallback) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return fallback;
  }
}
function writeJson(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(obj, null, 2), { mode: 0o600 });
}
function saveState() {
  state.seen = core ? core.seenList() : state.seen || [];
  writeJson(statePath(), state);
}

function loadConfig() {
  if (!fs.existsSync(configPath())) writeJson(configPath(), DEFAULT_CONFIG);
  configError = '';
  let c = {};
  try {
    c = JSON.parse(fs.readFileSync(configPath(), 'utf8'));
  } catch (e) {
    configError = `Settings file has an error, using defaults: ${e.message}`;
  }
  if (!c || typeof c !== 'object' || Array.isArray(c)) c = {};
  const out = { ...DEFAULT_CONFIG, ...c, ntfy: { ...DEFAULT_CONFIG.ntfy, ...(c.ntfy && typeof c.ntfy === 'object' ? c.ntfy : {}) } };
  // Type-check every field so one typo can't stop polling.
  const bad = [];
  const strList = (v) => Array.isArray(v) && v.every((x) => typeof x === 'string');
  for (const k of ['repos', 'authors']) if (!strList(out[k])) { bad.push(k); out[k] = []; }
  for (const k of ['label', 'shortcut']) if (typeof out[k] !== 'string') { bad.push(k); out[k] = DEFAULT_CONFIG[k]; }
  if (!(Number(out.pollSeconds) >= 10)) { if (out.pollSeconds !== undefined) bad.push('pollSeconds'); out.pollSeconds = 30; }
  for (const k of ['server', 'topic', 'token']) if (typeof out.ntfy[k] !== 'string') { bad.push(`ntfy.${k}`); out.ntfy[k] = DEFAULT_CONFIG.ntfy[k]; }
  if (bad.length && !configError) configError = `Ignored bad settings (${bad.join(', ')}); using defaults for those.`;
  return out;
}

// GUI apps on macOS don't inherit the shell PATH, so look where gh usually is.
function ghToken() {
  const PATH = ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', process.env.PATH || ''].join(path.delimiter);
  return new Promise((resolve) => {
    execFile('gh', ['auth', 'token'], { env: { ...process.env, PATH }, timeout: 5000 }, (err, out) => {
      resolve(err ? '' : String(out).trim());
    });
  });
}

async function resolveToken() {
  if (state.tokenEnc && safeStorage.isEncryptionAvailable()) {
    try {
      const t = safeStorage.decryptString(Buffer.from(state.tokenEnc, 'base64'));
      tokenSource = 'saved token';
      return t;
    } catch {
      // Happens after a rebuild changes the app's signature. Say so instead of silently switching.
      tokenWarning = 'Your saved GitHub token could not be unlocked (the app was probably rebuilt). Paste it again below.';
    }
  }
  for (const k of ['MOS_PAGER_GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN']) {
    if (process.env[k]) {
      tokenSource = k;
      return process.env[k];
    }
  }
  const t = await ghToken();
  tokenSource = t ? 'gh CLI' : '';
  return t;
}

async function buildSources() {
  const sources = [];
  const token = await resolveToken();
  if (token) sources.push(new GithubSource({ token, label: config.label, repos: config.repos, authors: config.authors, api: process.env.MOS_PAGER_GITHUB_API || undefined }));
  if (config.ntfy.topic) {
    sources.push(new NtfySource({ ...config.ntfy, dismissed: new Set(state.dismissed || []) }));
  }
  return sources;
}

// Each (re)start bumps the generation; a loop from an older generation stops,
// so reloading settings mid-poll never leaves two loops running.
async function startCore() {
  const gen = ++generation;
  clearTimeout(timer);
  tokenWarning = '';
  let sources = [];
  try {
    sources = await buildSources();
  } catch (e) {
    console.error('sources failed', e);
  }
  if (gen !== generation) return;
  clearTimeout(timer);
  const seen = core ? core.seenList() : state.seen || [];
  core = new PagerCore({
    sources,
    seen,
    onChange: (snap) => {
      const errors = [configError, tokenWarning, shortcutError, ...snap.errors].filter(Boolean);
      const needsSetup = !sources.length || snap.authError || !!tokenWarning;
      send('pages', { ...snap, errors, paused: !!state.paused, needsSetup, hasSavedToken: !!state.tokenEnc, tokenSource });
      updateTray(snap.pages.length);
    },
    onNew: (fresh) => notify(fresh),
  });
  loop(gen);
}

async function loop(gen) {
  if (gen !== generation) return;
  try {
    await core.poll();
    saveState();
  } catch (e) {
    console.error('poll failed', e);
  }
  if (gen !== generation) return;
  clearTimeout(timer);
  timer = setTimeout(() => loop(gen), config.pollSeconds * 1000);
}

function notify(fresh) {
  if (state.paused || !Notification.isSupported()) return;
  const loud = fresh.filter((p) => p.severity !== 'fyi');
  if (!loud.length) return;
  const p = loud[0];
  const n = new Notification({
    title: `${p.project ? p.project + ': ' : ''}${p.severity === 'blocker' ? 'Blocked on you' : 'Decision needed'}`,
    body: loud.length > 1 ? `${p.title} (+${loud.length - 1} more)` : p.title,
    silent: p.severity !== 'blocker',
  });
  // Hold a reference until it's dealt with, or it can be garbage-collected and the click lost.
  liveNotes.add(n);
  const drop = () => liveNotes.delete(n);
  n.on('click', () => {
    drop();
    showWindow();
    setExpanded(true);
  });
  n.on('close', drop);
  n.show();
}

function send(ch, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(ch, payload);
}

// ---------- window ----------
function defaultPos() {
  const wa = screen.getPrimaryDisplay().workArea;
  return { x: wa.x + wa.width - PET.w - 24, y: wa.y + wa.height - PET.h - 24 };
}

// Keep the pet's bottom-right corner fixed; the panel grows up and left.
function setExpanded(on) {
  if (!win) return;
  expanded = on;
  const b = win.getBounds();
  const right = b.x + b.width;
  const bottom = b.y + b.height;
  const size = on ? PANEL : PET;
  const wa = screen.getDisplayNearestPoint({ x: right - 1, y: bottom - 1 }).workArea;
  let x = right - size.w;
  let y = bottom - size.h;
  x = Math.min(Math.max(x, wa.x), wa.x + wa.width - size.w);
  y = Math.min(Math.max(y, wa.y), wa.y + wa.height - size.h);
  win.setBounds({ x, y, width: size.w, height: size.h });
  send('expanded', on);
  // Take keyboard focus while the panel is open so ⌘↵ and Esc work.
  if (on) win.focus();
}

function createWindow() {
  // A saved spot can be off-screen after unplugging a monitor.
  const onScreen = (p) => p && screen.getAllDisplays().some(({ workArea: w }) =>
    p.x >= w.x - PET.w / 2 && p.y >= w.y - PET.h / 2 && p.x <= w.x + w.width - PET.w / 2 && p.y <= w.y + w.height - PET.h / 2);
  const pos = onScreen(state.pos) ? state.pos : defaultPos();
  win = new BrowserWindow({
    ...pos,
    width: PET.w,
    height: PET.h,
    frame: false,
    transparent: true,
    resizable: false,
    hasShadow: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    fullscreenable: false,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.setAlwaysOnTop(true, 'floating');
  if (process.platform === 'darwin') win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.loadFile(path.join(__dirname, 'ui', 'index.html'));
  win.once('ready-to-show', () => {
    if (state.hidden) return;
    win.showInactive();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (e) => e.preventDefault());
  win.on('moved', () => {
    if (!expanded) {
      const b = win.getBounds();
      state.pos = { x: b.x, y: b.y };
      saveState();
    }
  });
}

function showWindow() {
  state.hidden = false;
  if (win) win.showInactive();
  saveState();
  rebuildTrayMenu();
}
function hideWindow() {
  state.hidden = true;
  if (expanded) setExpanded(false);
  if (win) win.hide();
  saveState();
  rebuildTrayMenu();
}
function toggleWindow() {
  if (win && win.isVisible()) hideWindow();
  else showWindow();
}
function setPaused(on) {
  state.paused = on;
  saveState();
  rebuildTrayMenu();
  if (core) core.onChange(core.snapshot());
}

// ---------- tray / menu bar ----------
// Drawn in code so there are no binary assets: a filled circle, with a ring when pages wait.
function trayIcon(count) {
  const s = 32;
  const buf = Buffer.alloc(s * s * 4);
  const c = s / 2;
  for (let y = 0; y < s; y++) {
    for (let x = 0; x < s; x++) {
      const d = Math.hypot(x + 0.5 - c, y + 0.5 - c);
      const fill = count ? d < 9 || (d > 12 && d < 15) : d < 11 && d > 7;
      const i = (y * s + x) * 4;
      buf[i + 3] = fill ? 255 : 0; // BGRA, black; template image recolors it
    }
  }
  const img = nativeImage.createFromBitmap(buf, { width: s, height: s, scaleFactor: 2 });
  if (process.platform === 'darwin') img.setTemplateImage(true);
  return img;
}

function updateTray(count) {
  if (!tray) return;
  tray.setImage(trayIcon(count));
  tray.setToolTip(count ? `MOS Pager: ${count} waiting` : 'MOS Pager: nothing waiting');
  if (process.platform === 'darwin') tray.setTitle(count ? String(count) : '');
}

function rebuildTrayMenu() {
  if (!tray) return;
  const visible = !state.hidden;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: visible ? 'Hide pet' : 'Show pet', accelerator: config.shortcut, click: toggleWindow },
    { label: 'Open pages', click: () => { showWindow(); setExpanded(true); } },
    { label: 'Do not disturb', type: 'checkbox', checked: !!state.paused, click: (i) => setPaused(i.checked) },
    { type: 'separator' },
    { label: 'Check now', click: () => core && core.poll().catch(() => {}) },
    {
      label: 'Start at login',
      type: 'checkbox',
      checked: app.getLoginItemSettings().openAtLogin,
      click: (i) => app.setLoginItemSettings({ openAtLogin: i.checked }),
    },
    { label: 'Forget saved GitHub token', enabled: !!state.tokenEnc, click: async () => { delete state.tokenEnc; saveState(); await startCore(); rebuildTrayMenu(); } },
    { label: 'Edit settings…', click: () => shell.openPath(configPath()) },
    { label: 'Reload settings', click: () => { config = loadConfig(); registerShortcut(); startCore(); rebuildTrayMenu(); } },
    { label: 'Reset pet position', click: () => { state.pos = defaultPos(); setExpanded(false); win.setPosition(state.pos.x, state.pos.y); saveState(); } },
    { type: 'separator' },
    { label: 'Quit MOS Pager', click: () => app.quit() },
  ]));
}

function registerShortcut() {
  globalShortcut.unregisterAll();
  if (config.shortcut) {
    let ok = false;
    try {
      ok = globalShortcut.register(config.shortcut, toggleWindow);
    } catch { /* invalid accelerator */ }
    shortcutError = ok ? '' : `Couldn't register the shortcut ${config.shortcut} (taken by another app?). Change "shortcut" in settings.`;
  } else {
    shortcutError = '';
  }
}

// ---------- IPC from the UI ----------
function wireIpc() {
  ipcMain.handle('toggle-panel', () => setExpanded(!expanded));
  ipcMain.handle('collapse', () => setExpanded(false));
  ipcMain.handle('hide', () => hideWindow());
  ipcMain.handle('set-paused', (_e, on) => setPaused(!!on));
  ipcMain.handle('refresh', () => core && core.poll());
  // Dragging uses the OS cursor position, not renderer coordinates, which
  // shift under the pointer as the window itself moves.
  let dragStart = null;
  ipcMain.handle('drag-start', () => {
    if (!win) return;
    const [x, y] = win.getPosition();
    dragStart = { cursor: screen.getCursorScreenPoint(), x, y };
  });
  ipcMain.handle('drag-move', () => {
    if (!win || !dragStart) return 0;
    const c = screen.getCursorScreenPoint();
    const dx = c.x - dragStart.cursor.x;
    const dy = c.y - dragStart.cursor.y;
    win.setPosition(dragStart.x + dx, dragStart.y + dy);
    return Math.abs(dx) + Math.abs(dy);
  });
  ipcMain.handle('drag-end', () => {
    dragStart = null;
    if (win && !expanded) {
      const [x, y] = win.getPosition();
      state.pos = { x, y };
      saveState();
    }
  });
  ipcMain.handle('open', (_e, url) => {
    const u = safeUrl(url);
    if (u) shell.openExternal(u);
  });
  ipcMain.handle('answer', async (_e, id, text, close) => {
    await core.answer(String(id), text ? String(text).slice(0, 4000) : '', { close: close !== false });
    if (String(id).startsWith('ntfy:')) {
      state.dismissed = [...(state.dismissed || []), String(id)].slice(-500);
    }
    saveState();
  });
  ipcMain.handle('save-token', async (_e, token) => {
    token = String(token || '').trim();
    if (!token) return;
    if (!safeStorage.isEncryptionAvailable()) throw new Error('No secure storage on this system; set GH_TOKEN or use `gh auth login` instead.');
    state.tokenEnc = safeStorage.encryptString(token).toString('base64');
    saveState();
    await startCore();
  });
  ipcMain.handle('forget-token', async () => {
    delete state.tokenEnc;
    saveState();
    await startCore();
  });
  ipcMain.handle('ready', () => core && core.onChange(core.snapshot()));
}

app.whenReady().then(async () => {
  if (process.platform === 'darwin' && app.dock) app.dock.hide();
  config = loadConfig();
  state = readJson(statePath(), {});
  wireIpc();
  createWindow();
  tray = new Tray(trayIcon(0));
  tray.setToolTip('MOS Pager');
  rebuildTrayMenu();
  registerShortcut();
  await startCore();
});

app.on('second-instance', () => showWindow());
app.on('window-all-closed', (e) => e.preventDefault());
app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (core) saveState();
});
