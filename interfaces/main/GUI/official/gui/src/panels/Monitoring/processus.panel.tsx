import React, { useEffect, useState } from 'react';
import { daemonPost } from '../../bridge.ts';
import type { PanelDef } from '../../types.ts';

export function ProcessesPanel() {
  const [procs, setProcs] = useState<any[]>([]);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const d = await daemonPost('system/processes', {});
        if (alive) { setProcs(d?.result?.processes || []); setErr(''); }
      } catch (e: any) {
        if (alive) setErr(e.message || 'daemon indisponible');
      }
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.7rem' }}>
      <h3 style={{ fontSize: '0.85rem', fontWeight: '600', marginBottom: '0.2rem' }}>Processus (top CPU)</h3>
      <div style={{ fontSize: '0.62rem', color: '#475569', marginBottom: '0.5rem', fontFamily: 'monospace' }}>30 premiers · maj 3s</div>
      {err && <div style={{ color: '#fca5a5', fontSize: '0.7rem', marginBottom: '0.4rem' }}>{err}</div>}
      <div style={{ maxHeight: '14rem', overflow: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.66rem' }}>
          <thead>
            <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
              <th style={{ textAlign: 'left', padding: '0.15rem 0.4rem' }}>PID</th>
              <th style={{ textAlign: 'left', padding: '0.15rem 0.4rem' }}>Nom</th>
              <th style={{ textAlign: 'right', padding: '0.15rem 0.4rem' }}>CPU %</th>
              <th style={{ textAlign: 'right', padding: '0.15rem 0.4rem' }}>RAM</th>
              <th style={{ textAlign: 'left', padding: '0.15rem 0.4rem' }}>Commande</th>
            </tr>
          </thead>
          <tbody>
            {procs.map((p: any) => (
              <tr key={p.pid} style={{ borderBottom: '1px solid #1e293b', color: '#e2e8f0' }}>
                <td style={{ padding: '0.15rem 0.4rem' }}>{p.pid}</td>
                <td style={{ padding: '0.15rem 0.4rem', fontWeight: '600' }}>{p.name}</td>
                <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right', color: (p.cpu ?? 0) > 50 ? '#f59e0b' : '#6ee7b7' }}>{(p.cpu ?? 0).toFixed(1)}</td>
                <td style={{ padding: '0.15rem 0.4rem', textAlign: 'right' }}>{p.rss_kb ? (p.rss_kb / 1024).toFixed(0) + ' Mo' : '—'}</td>
                <td style={{ padding: '0.15rem 0.4rem', color: '#64748b', maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.command}</td>
              </tr>
            ))}
            {procs.length === 0 && (
              <tr><td colSpan={5} style={{ padding: '0.5rem', textAlign: 'center', color: '#64748b' }}>Aucun processus</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: "monitoring/processus", label: "Processus", icon: "memory", version: "1.0.0",
  description: "Top processus système par utilisation CPU",
  daemonRoutes: [{ route: "system/processes", methods: ["GET"], desc: "Top processus" }],
  menu: [],
  declaration: () => "[monitoring/processus] Processus v1.0.0\n  Routes: system/processes",
  component: ({ ctx }) => React.createElement(ProcessesPanel),
};
