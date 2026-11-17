'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pager', {
  togglePanel: () => ipcRenderer.invoke('toggle-panel'),
  collapse: () => ipcRenderer.invoke('collapse'),
  hide: () => ipcRenderer.invoke('hide'),
  setPaused: (on) => ipcRenderer.invoke('set-paused', on),
  refresh: () => ipcRenderer.invoke('refresh'),
  dragStart: () => ipcRenderer.invoke('drag-start'),
  dragMove: () => ipcRenderer.invoke('drag-move'),
  dragEnd: () => ipcRenderer.invoke('drag-end'),
  open: (url) => ipcRenderer.invoke('open', url),
  answer: (id, text, close) => ipcRenderer.invoke('answer', id, text, close),
  saveToken: (t) => ipcRenderer.invoke('save-token', t),
  forgetToken: () => ipcRenderer.invoke('forget-token'),
  ready: () => ipcRenderer.invoke('ready'),
  onPages: (fn) => ipcRenderer.on('pages', (_e, d) => fn(d)),
  onExpanded: (fn) => ipcRenderer.on('expanded', (_e, d) => fn(d)),
});
