// Bridge V2 minimal pour panels standalone (fallback HTTP).
// Réutilise la config daemon si dispo, sinon defaults.

let _daemonToken = '';
let _daemonPort = 8770;

async function ensureDaemonConfig(): Promise<void> {
  if (_daemonToken) return;
  try {
    // Tentative de lecture depuis la config globale si exposée.
    const cfg = (globalThis as any).__DAEMON_CONFIG__;
    if (cfg) {
      _daemonToken = cfg.token || '';
      _daemonPort = cfg.port || 8770;
    }
  } catch {
    // fallback defaults
  }
}

export async function daemonPost(route: string, body: any): Promise<any> {
  await ensureDaemonConfig();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (_daemonToken) headers['Authorization'] = `Bearer ${_daemonToken}`;
  const url = `http://127.0.0.1:${_daemonPort}/v1/${route}`;
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} ${route}: ${text.substring(0, 100)}`);
  }
  return res.json();
}
