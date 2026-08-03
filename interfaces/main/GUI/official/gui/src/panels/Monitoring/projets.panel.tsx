import React, { useEffect, useState } from 'react';
import { daemonPost } from '../../bridge.ts';
import type { PanelDef } from '../../types.ts';

export function ProjectsPanel() {
  const [teams, setTeams] = useState<any[]>([]);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await daemonPost('team/list', {});
        if (alive) { setTeams(d?.teams || []); setErr(''); }
      } catch (e: any) {
        if (alive) setErr(e.message || 'daemon indisponible');
      }
    };
    load();
    const t = setInterval(load, 10000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const statusColor = (s: string) =>
    s === 'ready' ? '#22c55e' : s === 'running' ? '#3b82f6' : s === 'error' ? '#ef4444' : '#f59e0b';

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.7rem' }}>
      <h3 style={{ fontSize: '0.85rem', fontWeight: '600', marginBottom: '0.2rem' }}>Projets & équipes</h3>
      <div style={{ fontSize: '0.62rem', color: '#475569', marginBottom: '0.5rem', fontFamily: 'monospace' }}>workspaces · maj 10s</div>
      {err && <div style={{ color: '#fca5a5', fontSize: '0.7rem', marginBottom: '0.4rem' }}>{err}</div>}
      <div style={{ maxHeight: '14rem', overflow: 'auto' }}>
        {teams.map((t: any, i: number) => (
          <div key={t.name || i} style={{ borderBottom: '1px solid #1e293b', padding: '0.3rem 0', fontSize: '0.7rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: '600', color: '#e2e8f0' }}>{t.name}</span>
              <span style={{ color: statusColor(t.status), fontSize: '0.62rem', border: '1px solid #334155', borderRadius: '0.25rem', padding: '0.05rem 0.35rem' }}>
                {t.status || '?'}
              </span>
            </div>
            <div style={{ color: '#64748b', fontSize: '0.62rem', marginTop: '0.15rem' }}>
              {t.topology || 'topology ?'} · {t.workspace_id ? `ws ${t.workspace_id}` : ''}
              {' · '}{(t.members || []).length} membre(s)
              {t.team_leader ? ` · lead ${t.team_leader.agent_name}` : ''}
            </div>
          </div>
        ))}
        {teams.length === 0 && !err && (
          <div style={{ color: '#94a3b8', fontSize: '0.7rem', padding: '0.5rem' }}>Aucune équipe active</div>
        )}
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: "monitoring/projets", label: "Projets", icon: "workspaces", version: "1.0.0",
  description: "Projets & équipes : état des workspaces",
  daemonRoutes: [{ route: "team/list", methods: ["GET"], desc: "Liste équipes" }],
  menu: [],
  declaration: () => "[monitoring/projets] Projets v1.0.0\n  Routes: team/list",
  component: ({ ctx }) => React.createElement(ProjectsPanel),
};
