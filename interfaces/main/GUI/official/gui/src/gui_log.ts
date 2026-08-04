// gui_log — Journal détaillé (full-log) de la GUI.
//
// Mode "full-log" : décrit en txt ce qui se passe côté GUI (chargement des
// éléments, panels, fenêtres, erreurs…). Activé par :
//   - ?full-log=1 dans l'URL, OU
//   - window.__MW_FULL_LOG = true
//   - fullLog = true dans la config
//
// Les événements sont envoyés au daemon (route logs/write) qui les écrit dans
// ~/.modelweaver/logs/installer.log (reuse log_to_file). Le nom de fichier
// dédié est passé en paramètre optionnel.

import { daemonPost } from './bridge.ts';

let _enabled: boolean | null = null;

export function isFullLogEnabled(): boolean {
  if (_enabled !== null) return _enabled;
  try {
    if (typeof window !== 'undefined') {
      if ((window as any).__MW_FULL_LOG === true) { _enabled = true; return true; }
      const p = new URLSearchParams(window.location.search);
      if (p.get('full-log') === '1') { _enabled = true; return true; }
    }
  } catch { /* ignore */ }
  _enabled = false;
  return false;
}

export function setFullLog(v: boolean): void {
  _enabled = v;
}

let _pending: string[] = [];
let _flushing = false;

function flush() {
  if (_flushing || _pending.length === 0) return;
  _flushing = true;
  const batch = _pending;
  _pending = [];
  daemonPost('logs/write', {
    level: 'GUI',
    message: '[full-log] ' + batch.join(' | '),
  }).finally(() => { _flushing = false; if (_pending.length) flush(); });
}

/** Trace un événement GUI dans le full-log (best-effort, ne casse jamais). */
export function logGui(event: string, detail?: unknown): void {
  if (!isFullLogEnabled()) return;
  const detailStr = detail === undefined ? '' : ` :: ${safeStr(detail)}`;
  _pending.push(`${ts()} ${event}${detailStr}`);
  if (_pending.length >= 10) flush();
}

function ts(): string {
  return new Date().toISOString().slice(11, 23);
}

function safeStr(v: unknown): string {
  try {
    if (typeof v === 'string') return v.slice(0, 200);
    return JSON.stringify(v)?.slice(0, 200) ?? '';
  } catch { return ''; }
}

// Assurer un flush final au déchargement (best-effort).
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => flush());
}
