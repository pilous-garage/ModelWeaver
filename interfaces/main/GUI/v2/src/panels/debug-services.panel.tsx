import React, { useEffect, useState, useCallback } from 'react';
import { daemonPost } from '../bridge.ts';
import type { PanelDef, PanelContext } from '../types.ts';

type ServiceStatus = 'running' | 'stopped' | 'crashed' | 'restarting';
type ServiceItem = {
  name: string;
  mode: string;
  status: ServiceStatus;
  pid: number | null;
  restarts: number;
  last_exit: number | null;
  started_at: number;
};

function timeAgo(ts: number | null): string {
  if (!ts) return '—';
  const diff = Math.max(0, Math.floor((Date.now() / 1000) - ts));
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  return `${Math.floor(diff / 3600)}h`;
}

function statusColor(status: ServiceStatus): string {
  switch (status) {
    case 'running':
      return '#22c55e';
    case 'stopped':
      return '#64748b';
    case 'crashed':
      return '#ef4444';
    case 'restarting':
      return '#f59e0b';
    default:
      return '#64748b';
  }
}

function ServicesDebugPanel({ ctx }: { ctx: PanelContext }) {
  const [services, setServices] = useState<ServiceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [logName, setLogName] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);

  const fetchServices = useCallback(async () => {
    try {
      const data = await daemonPost('service/list', {});
      if (data?.ok && Array.isArray(data.result?.services)) {
        setServices(data.result.services);
        setError(null);
      } else {
        setError('Réponse inattendue du daemon');
      }
    } catch (e: any) {
      setError(e?.message || 'daemon indisponible');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServices();
    const t = setInterval(fetchServices, 3000);
    return () => clearInterval(t);
  }, [fetchServices]);

  const handleAction = async (name: string, route: string) => {
    setActionLoading(`${route}:${name}`);
    try {
      await daemonPost(route, { name });
      await fetchServices();
    } catch (e: any) {
      setError(e?.message || 'action échouée');
    } finally {
      setActionLoading(null);
    }
  };

  const handleViewLog = async (name: string) => {
    setLogName(name);
    setLogLines([]);
    try {
      const data = await daemonPost('service_log', { name, lines: 40 });
      const text = typeof data?.result === 'string' ? data.result : '';
      setLogLines(text.split('\n').filter(Boolean).slice(-40));
    } catch {
      setLogLines(['[impossible de charger le log]']);
    }
  };

  const restartAll = async () => {
    setActionLoading('restart-all');
    try {
      await Promise.all(services.map(s => daemonPost('service/restart', { name: s.name })));
      await fetchServices();
    } catch (e: any) {
      setError(e?.message || 'redémarrage global échoué');
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div style={{ backgroundColor: '#0f172a', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.6rem', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
        <div>
          <div style={{ fontSize: '0.8rem', fontWeight: 700 }}>Services du daemon</div>
          <div style={{ fontSize: '0.65rem', color: '#94a3b8' }}>{services.length} service(s) · maj auto 3s</div>
        </div>
        <button
          onClick={restartAll}
          disabled={!!actionLoading || services.length === 0}
          style={{
            fontSize: '0.65rem',
            padding: '0.25rem 0.6rem',
            backgroundColor: actionLoading === 'restart-all' ? '#475569' : '#1d4ed8',
            color: '#e2e8f0',
            border: 'none',
            borderRadius: '0.3rem',
            cursor: services.length === 0 ? 'not-allowed' : 'pointer',
            opacity: services.length === 0 ? 0.6 : 1,
          }}
        >
          {actionLoading === 'restart-all' ? '...' : 'Redémarrer tout'}
        </button>
      </div>

      {error && <div style={{ color: '#fca5a5', fontSize: '0.7rem', marginBottom: '0.4rem' }}>{error}</div>}

      {loading ? (
        <div style={{ color: '#94a3b8', fontSize: '0.7rem' }}>Chargement…</div>
      ) : (
        <div style={{ overflow: 'auto', maxHeight: '14rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.68rem' }}>
            <thead>
              <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                <th style={{ textAlign: 'left', padding: '0.15rem 0.4rem' }}>Service</th>
                <th style={{ textAlign: 'left', padding: '0.15rem 0.4rem' }}>Mode</th>
                <th style={{ textAlign: 'center', padding: '0.15rem 0.4rem' }}>Statut</th>
                <th style={{ textAlign: 'right', padding: '0.15rem 0.4rem' }}>PID</th>
                <th style={{ textAlign: 'right', padding: '0.15rem 0.4rem' }}>Redém.</th>
                <th style={{ textAlign: 'right', padding: '0.15rem 0.4rem' }}>Démarré</th>
                <th style={{ textAlign: 'center', padding: '0.15rem 0.4rem' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {services.map((s) => (
                <tr key={s.name} style={{ borderBottom: '1px solid #1e293b' }}>
                  <td style={{ padding: '0.15rem 0.4rem', fontWeight: 600 }}>{s.name}</td>
                  <td style={{ padding: '0.15rem 0.4rem', color: '#cbd5e1' }}>{s.mode}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'center' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
                      <span
                        style={{
                          display: 'inline-block',
                          width: '8px',
                          height: '8px',
                          borderRadius: '50%',
                          backgroundColor: statusColor(s.status),
                        }}
                      />
                      {s.status}
                    </span>
                  </td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right', color: '#cbd5e1' }}>{s.pid ?? '—'}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right', color: '#cbd5e1' }}>{s.restarts ?? 0}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right', color: '#94a3b8' }}>{timeAgo(s.started_at)}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'center', whiteSpace: 'nowrap' }}>
                    {s.status === 'stopped' ? (
                      <button
                        onClick={() => handleAction(s.name, 'service/start')}
                        disabled={!!actionLoading}
                        style={{ fontSize: '0.6rem', padding: '0.15rem 0.4rem', backgroundColor: '#059669', color: '#e2e8f0', border: 'none', borderRadius: '0.2rem', cursor: 'pointer', marginRight: '0.2rem' }}
                      >
                        Démarrer
                      </button>
                    ) : (
                      <>
                        <button
                          onClick={() => handleAction(s.name, 'service/restart')}
                          disabled={!!actionLoading}
                          style={{ fontSize: '0.6rem', padding: '0.15rem 0.4rem', backgroundColor: '#1d4ed8', color: '#e2e8f0', border: 'none', borderRadius: '0.2rem', cursor: 'pointer', marginRight: '0.2rem' }}
                        >
                          Redémarrer
                        </button>
                        <button
                          onClick={() => handleAction(s.name, 'service/stop')}
                          disabled={!!actionLoading}
                          style={{ fontSize: '0.6rem', padding: '0.15rem 0.4rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: 'none', borderRadius: '0.2rem', cursor: 'pointer', marginRight: '0.2rem' }}
                        >
                          Arrêter
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => handleViewLog(s.name)}
                      style={{ fontSize: '0.6rem', padding: '0.15rem 0.4rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.2rem', cursor: 'pointer' }}
                    >
                      Log
                    </button>
                  </td>
                </tr>
              ))}
              {services.length === 0 && (
                <tr>
                  <td colSpan={7} style={{ padding: '0.5rem', textAlign: 'center', color: '#64748b' }}>
                    Aucun service supervisé
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {logName && (
        <div style={{ marginTop: '0.6rem', borderTop: '1px solid #334155', paddingTop: '0.4rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.3rem' }}>
            <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#93c5fd' }}>
              Log : {logName}
            </div>
            <button
              onClick={() => { setLogName(null); setLogLines([]); }}
              style={{ fontSize: '0.6rem', padding: '0.15rem 0.4rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.2rem', cursor: 'pointer' }}
            >
              Fermer
            </button>
          </div>
          <pre
            style={{
              backgroundColor: '#020617',
              border: '1px solid #1e293b',
              borderRadius: '0.35rem',
              padding: '0.4rem',
              maxHeight: '10rem',
              overflow: 'auto',
              fontSize: '0.62rem',
              color: '#cbd5e1',
              margin: 0,
            }}
          >
            {logLines.length === 0 ? 'Aucune ligne' : logLines.join('\n')}
          </pre>
        </div>
      )}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'debug-services',
  label: 'Services',
  icon: 'monitor_heart',
  version: '2.0.0',
  description: 'Supervision avancée des services du daemon',
  descriptionLong: 'Liste des services supervisés, états, PID, redémarrages, logs récents et actions individuelles ou globales.',
  idWarning: 'debug-services',
  defaultSize: { width: 720, height: 420 },
  daemonRoutes: [
    { route: 'service/list', methods: ['GET'], desc: 'Liste des services', params: {} },
    { route: 'service/restart', methods: ['POST'], desc: 'Redémarrer un service', params: { name: 'string' } },
    { route: 'service/stop', methods: ['POST'], desc: 'Arrêter un service', params: { name: 'string' } },
    { route: 'service/start', methods: ['POST'], desc: 'Démarrer un service', params: { name: 'string' } },
    { route: 'service_log', methods: ['POST'], desc: 'Lire le log d’un service', params: { name: 'string', lines: 'number' } },
  ],
  menu: [
    { menuPath: ['Debug'], id: 'services:restart-all', label: 'Redémarrer tout', shortcut: 'Ctrl+Shift+R', type: 'normal', action: 'panel:services:restart-all' },
    { menuPath: ['Debug'], id: 'services:refresh', label: 'Rafraîchir', shortcut: 'F5', type: 'normal', action: 'panel:services:refresh' },
  ],
  declaration: () =>
    '[debug-services] Services V2\n' +
    '  Routes: service/list, service/restart, service/stop, service/start, service_log\n' +
    '  Features: status color, per-service actions, global restart, inline log viewer',
  onActivate(ctx: any) {
    ctx?.onMenuAction?.('panel:services:refresh');
  },
  onDeactivate() {
    // noop
  },
  onRefresh() {
    return Promise.resolve();
  },
  component: () => React.createElement(ServicesDebugPanel),
};
