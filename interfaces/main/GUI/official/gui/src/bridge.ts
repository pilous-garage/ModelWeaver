// Pont Tauri/Web — l'API Tauri est indisponible dans un navigateur standard.
// Détection automatique + fallback HTTP vers le daemon.

type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<any>;

let _hasTauri = typeof window !== 'undefined' && !!(window as any).__TAURI_INTERNALS__;
let _tauriCore: any = null;
let _tauriWindow: any = null;

async function loadModules() {
  if (!_hasTauri) return;
  if (_tauriCore === null) {
    try {
      _tauriCore = await import('@tauri-apps/api/core');
      _tauriWindow = await import('@tauri-apps/api/window');
    } catch {
      _hasTauri = false;
      _tauriCore = false;
      _tauriWindow = false;
    }
  }
}

const DAEMON_URL = 'http://127.0.0.1:8771';

async function httpPost(route: string, body: unknown): Promise<any> {
  const res = await fetch(`${DAEMON_URL}/${route}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${route}`);
  return res.json();
}

export async function daemonPost(route: string, body: any): Promise<any> {
  if (!_hasTauri) return httpPost(route, body);
  await loadModules();
  if (_tauriCore) return _tauriCore.invoke('daemon_post', { route, body: JSON.stringify(body) });
  return httpPost(route, body);
}

export async function invoke(cmd: string, args?: Record<string, unknown>): Promise<any> {
  if (!_hasTauri) {
    if (cmd === 'daemon_post') {
      const r = args as any;
      return httpPost(r.route, JSON.parse(r.body));
    }
    console.warn(`[bridge] invoke("${cmd}") sans Tauri — ignoré`);
    return undefined;
  }
  await loadModules();
  if (_tauriCore) return _tauriCore.invoke(cmd, args);
  console.warn(`[bridge] invoke("${cmd}") modules non chargés — ignoré`);
  return undefined;
}

export async function getWindowLabel(): Promise<string> {
  if (!_hasTauri) return 'main';
  await loadModules();
  if (_tauriWindow) {
    const w = _tauriWindow.getCurrentWindow();
    return w.label;
  }
  return 'main';
}

class MockLogicalSize { constructor(public w: number, public h: number) {} }

class MockWindow {
  label = 'main';
  async setSize(_size?: any) {}
  async setFullscreen(_fullscreen?: boolean) {}
}
export function getCurrentWindow() {
  if (_hasTauri) {
    try {
      // Synchronous access via __TAURI_INTERNALS__
      const meta = (window as any).__TAURI_INTERNALS__?.metadata;
      if (meta?.currentWindow?.label) return { label: meta.currentWindow.label, setSize: async () => {}, setFullscreen: async () => {} };
    } catch {}
  }
  return new MockWindow();
}

export { MockLogicalSize as LogicalSize };

class MockWebviewWindow {
  constructor(_label: string, _options?: any) {}
  static getByLabel(_label: string) { return null; }
}
export async function getWebviewWindowClass() {
  if (_hasTauri) {
    await loadModules();
    try {
      const wv = await import('@tauri-apps/api/webviewWindow');
      return wv.WebviewWindow;
    } catch {}
  }
  return MockWebviewWindow;
}
