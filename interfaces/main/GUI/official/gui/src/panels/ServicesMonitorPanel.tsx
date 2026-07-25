import React, { useState, useEffect } from 'react';
import type { AppApi } from '../useApp.ts';
import { invoke } from '../bridge.ts';

type TabId = 'processors' | 'services' | 'logs';

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      style={{
        padding: '0.2rem 0.6rem',
        fontSize: '0.68rem',
        border: 'none',
        borderRadius: '0.3rem 0.3rem 0 0',
        cursor: 'pointer',
        backgroundColor: active ? '#1e40af' : '#334155',
        color: active ? '#e2e8f0' : '#94a3b8',
        fontWeight: active ? '600' : 'normal',
      }}>
      {children}
    </button>
  );
}

function LogViewer({ logPath, procId }: { logPath?: string; procId?: number }) {
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    if (!logPath && !procId) return;
    const fetchLog = async () => {
      try {
        const res = logPath
          ? await fetch(`http://127.0.0.1:8770/v1/supervisor/log?name=${encodeURIComponent(logPath)}&lines=50`)
          : null;
        // Fallback: use invoke for service log
      } catch {}
    };
    fetchLog();
    const t = setInterval(fetchLog, 3000);
    return () => clearInterval(t);
  }, [logPath, procId]);
  return (
    <div style={{ fontSize: '0.65rem', fontFamily: 'monospace', whiteSpace: 'pre', overflow: 'auto', maxHeight: '80px', color: '#94a3b8' }}>
      {lines.length === 0 ? 'Aucun log récent' : lines.join('\n')}
    </div>
  );
}

export function ServicesMonitorPanel({ app }: { app: AppApi }) {
  const [tab, setTab] = useState<TabId>('services');

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.6rem', flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: '0.2rem', marginBottom: '0.4rem', flexShrink: 0 }}>
        <TabButton active={tab === 'processors'} onClick={() => setTab('processors')}>Processors</TabButton>
        <TabButton active={tab === 'services'} onClick={() => setTab('services')}>Services</TabButton>
        <TabButton active={tab === 'logs'} onClick={() => setTab('logs')}>Logs</TabButton>
      </div>

      {tab === 'processors' && (
        <div style={{ flex: 1, overflow: 'auto', fontSize: '0.68rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                <th style={{ textAlign: 'left', padding: '0.2rem 0.4rem' }}>PID</th>
                <th style={{ textAlign: 'left', padding: '0.2rem 0.4rem' }}>Nom</th>
                <th style={{ textAlign: 'right', padding: '0.2rem 0.4rem' }}>CPU %</th>
                <th style={{ textAlign: 'right', padding: '0.2rem 0.4rem' }}>RAM (KB)</th>
                <th style={{ textAlign: 'left', padding: '0.2rem 0.4rem' }}>Commande</th>
              </tr>
            </thead>
            <tbody>
              {(app.procList || []).map((p: any) => (
                <tr key={p.id} style={{ borderBottom: '1px solid #1e293b', color: '#e2e8f0' }}>
                  <td style={{ padding: '0.15rem 0.4rem' }}>{p.pid ?? '—'}</td>
                  <td style={{ padding: '0.15rem 0.4rem', fontWeight: '600' }}>{p.name}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right', color: p.cpu > 50 ? '#f59e0b' : '#6ee7b7' }}>{p.cpu.toFixed(1)}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right' }}>{p.rss_kb ? (p.rss_kb / 1024).toFixed(0) + ' Mo' : '—'}</td>
                  <td style={{ padding: '0.15rem 0.4rem', color: '#64748b', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.command}</td>
                </tr>
              ))}
              {(app.procList || []).length === 0 && (
                <tr><td colSpan={5} style={{ padding: '0.5rem', textAlign: 'center', color: '#64748b' }}>Aucun processus surveillé</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'services' && (
        <div style={{ flex: 1, overflow: 'auto', fontSize: '0.68rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                <th style={{ textAlign: 'left', padding: '0.2rem 0.4rem' }}>Service</th>
                <th style={{ textAlign: 'left', padding: '0.2rem 0.4rem' }}>Mode</th>
                <th style={{ textAlign: 'center', padding: '0.2rem 0.4rem' }}>Statut</th>
                <th style={{ textAlign: 'right', padding: '0.2rem 0.4rem' }}>PID</th>
                <th style={{ textAlign: 'right', padding: '0.2rem 0.4rem' }}>Redém.</th>
              </tr>
            </thead>
            <tbody>
              {(app.serviceList || []).map((s: any) => (
                <tr key={s.name} style={{ borderBottom: '1px solid #1e293b', color: '#e2e8f0' }}>
<td style={{ padding: '0.15rem 0.4rem', fontWeight: '600' }}>
                      {s.name}
                      <button onClick={async () => {
                        try { await invoke<any>('daemon_post', { route: 'service/restart', body: JSON.stringify({ name: s.name }) }); } catch {}
                      }}
                        style={{ marginLeft: '0.3rem', fontSize: '0.58rem', padding: '0.1rem 0.3rem', backgroundColor: '#1d4ed8', color: '#e2e8f0', border: 'none', borderRadius: '0.2rem', cursor: 'pointer' }}>
                        ⟳
                      </button>
                      <button onClick={async () => {
                        try { await invoke<any>('daemon_post', { route: 'service/stop', body: JSON.stringify({ name: s.name }) }); } catch {}
                      }}
                        style={{ marginLeft: '0.2rem', fontSize: '0.58rem', padding: '0.1rem 0.3rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: 'none', borderRadius: '0.2rem', cursor: 'pointer' }}>
                        ■
                      </button>
                    </td>
                  <td style={{ padding: '0.15rem 0.4rem' }}>{s.mode}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'center' }}>
                    <span style={{
                      display: 'inline-block',
                      width: '8px', height: '8px',
                      borderRadius: '50%',
                      backgroundColor: s.status === 'running' ? '#22c55e' : s.status === 'crashed' ? '#ef4444' : s.status === 'restarting' ? '#f59e0b' : '#64748b',
                    }} /> {s.status}
                  </td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right' }}>{s.pid ?? '—'}</td>
                  <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right' }}>{s.restarts ?? 0}</td>
                </tr>
              ))}
              {(app.serviceList || []).length === 0 && (
                <tr><td colSpan={5} style={{ padding: '0.5rem', textAlign: 'center', color: '#64748b' }}>Aucun service supervisé</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'logs' && (
        <div style={{ flex: 1, overflow: 'auto', fontSize: '0.65rem' }}>
          {(app.serviceList || []).map((s: any) => (
            <div key={s.name} style={{ marginBottom: '0.3rem', borderBottom: '1px solid #1e293b', paddingBottom: '0.3rem' }}>
              <div style={{ fontWeight: '600', color: '#93c5fd', marginBottom: '0.15rem' }}>{s.name} <span style={{ color: '#64748b', fontWeight: 'normal' }}>· {s.status}</span></div>
               <button onClick={async () => {
                 try { await invoke<any>('service_log', { name: s.name, lines: 20 }); } catch {}
               }}
                style={{ fontSize: '0.6rem', padding: '0.1rem 0.4rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.2rem', cursor: 'pointer' }}>
                Voir log
              </button>
            </div>
          ))}
          {(app.serviceList || []).length === 0 && (
            <div style={{ color: '#64748b', textAlign: 'center', padding: '0.5rem' }}>Aucun service</div>
          )}
        </div>
      )}
    </div>
  );
}
