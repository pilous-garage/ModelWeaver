// Bridge — IPC Tauri + accès au daemon HTTP, avec fallback web (tests).

export interface DaemonConfig {
  token: string;
  port: number;
}

let _config: DaemonConfig | null = null;
let _hasTauri = typeof window !== 'undefined' && !!(window as any).__TAURI_INTERNALS__;

async function getConfig(force = false): Promise<DaemonConfig> {
  if (_config && !force) return _config;
  // En Tauri : la config réelle vient du backend (token + port du daemon).
  // En web (tests/dev sans Tauri) : fallback port 8770 sans token.
  if (_hasTauri) {
    try {
      const core = await import('@tauri-apps/api/core');
      const cfg = await core.invoke<DaemonConfig>('daemon_config');
      if (cfg && cfg.port) {
        _config = { token: cfg.token || '', port: Number(cfg.port) };
        return _config;
      }
    } catch {
      // fallback ci-dessous
    }
  }
  _config = { token: '', port: 8770 };
  return _config;
}

/** POST vers le daemon (route v1). En mode web (tests), fallback mock. */
export async function daemonPost(route: string, body: any): Promise<any> {
  let cfg = await getConfig();
  try {
    return await postOnce(cfg, route, body);
  } catch (e: any) {
    // Le daemon peut avoir changé de port (superviseur l'a relancé sur un autre
    // port) : on relit la config fraîche et on retente UNE fois. C'est le
    // mécanisme de transmission dynamique de l'adresse du daemon à la GUI.
    if (_hasTauri) {
      try {
        cfg = await getConfig(true);
        if (cfg.port && cfg.port !== _config?.port) {
          return await postOnce(cfg, route, body);
        }
      } catch {
        // best-effort
      }
    }
    // Fallback : si le daemon n'est pas joignable (test sans backend), on
    // renvoie un résultat neutre pour ne pas casser les panels.
    if (import.meta.env?.DEV && !_hasTauri) {
      return { ok: true, route, result: null };
    }
    throw e;
  }
}

async function postOnce(cfg: DaemonConfig, route: string, body: any): Promise<any> {
  const res = await fetch(`http://127.0.0.1:${cfg.port}/v1/${route}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} ${route}: ${text.slice(0, 120)}`);
  }
  return res.json();
}

/** URL de base du daemon (http://127.0.0.1:<port>) pour les imports distants. */
export async function daemonBaseUrl(): Promise<string> {
  const cfg = await getConfig();
  return `http://127.0.0.1:${cfg.port}`;
}

/** Invoke Tauri (si dispo), sinon no-op. */
export async function invoke(cmd: string, args?: Record<string, unknown>): Promise<any> {
  if (_hasTauri) {
    try {
      const core = await import('@tauri-apps/api/core');
      return core.invoke(cmd, args);
    } catch {
      return undefined;
    }
  }
  return undefined;
}

/** Ouvre une fenêtre Tauri (ou no-op en web). */
export async function createWindow(label: string, opts?: { size?: { width: number; height: number }; pos?: { x: number; y: number }; title?: string }): Promise<any> {
  return invoke('create_window', { label, ...(opts?.size ? { size: opts.size } : {}), ...(opts?.pos ? { pos: opts.pos } : {}), ...(opts?.title ? { title: opts.title } : {}) });
}

/** Ferme une fenêtre précise. */
export async function closeWindow(label: string): Promise<any> {
  return invoke('close_window', { label });
}

/** Ferme la fenêtre courante. */
export async function closeCurrentWindow(): Promise<any> {
  return invoke('close_current_window');
}

/** Met une fenêtre au premier plan. */
export async function focusWindow(label: string): Promise<any> {
  return invoke('focus_window', { label });
}

/** Liste les fenêtres Tauri réellement ouvertes. */
export async function listWindows(): Promise<{ label: string; title: string }[]> {
  const res = await invoke('list_windows') as any;
  return Array.isArray(res?.windows) ? res.windows : [];
}

/** Position/taille/état de la fenêtre courante. */
export async function currentWindowState(): Promise<{ x?: number; y?: number; width?: number; height?: number; fullscreen: boolean; maximized: boolean }> {
  const res = await invoke('window_state') as any;
  return res ?? { fullscreen: false, maximized: false };
}

/** Bascule le plein écran de la fenêtre courante. */
export async function toggleFullscreen(): Promise<boolean> {
  const res = await invoke('window_fullscreen') as any;
  return Boolean(res?.fullscreen);
}
