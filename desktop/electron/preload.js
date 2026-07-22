'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ai8VideoElectron', {
  getConfig() {
    return ipcRenderer.invoke('desktop:get-config');
  },
  pickPython() {
    return ipcRenderer.invoke('desktop:pick-python');
  },
  pickProjectDir() {
    return ipcRenderer.invoke('desktop:pick-project-dir');
  },
  startBackend(settings) {
    return ipcRenderer.invoke('desktop:start-backend', settings || {});
  },
  openExternal(url) {
    return ipcRenderer.invoke('desktop:open-external', String(url || ''));
  },
});
