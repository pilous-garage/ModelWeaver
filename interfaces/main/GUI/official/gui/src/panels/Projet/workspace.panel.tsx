import React, { useState } from 'react';
import { daemonPost } from '../../bridge.ts';
import type { PanelDef } from '../../types.ts';

function WorkspacePanel() {
  const [teamName, setTeamName] = useState('');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const handleInit = async () => {
    setLoading(true);
    try { const r = await daemonPost('team/init-workspace', { name: teamName }); setResult(r); }
    catch (e: any) { setResult({ error: e.message }); }
    setLoading(false);
  };
  return React.createElement('div', { style: { padding: '0.5rem', fontSize: '0.78rem' } },
    React.createElement('h3', { style: { fontWeight: 600, marginBottom: '0.5rem' } }, '🏗️ Workspace'),
    React.createElement('input', {
      value: teamName, placeholder: "Nom de l'équipe",
      onChange: (e: any) => setTeamName(e.target.value),
      style: { width: '100%', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', padding: '0.4rem', fontSize: '0.78rem', marginBottom: '0.5rem' },
    }),
    React.createElement('button', {
      onClick: handleInit, disabled: loading || !teamName,
      style: { padding: '0.3rem 0.8rem', backgroundColor: loading ? '#334155' : '#1d4ed8', color: '#e2e8f0', border: 'none', borderRadius: '0.3rem', cursor: loading ? 'not-allowed' : 'pointer' },
    }, loading ? '⏳…' : '🚀 Initialiser le workspace'),
    result && React.createElement('pre', { style: { marginTop: '0.5rem', fontSize: '0.65rem', color: result.error ? '#fca5a5' : '#6ee7b7' } }, JSON.stringify(result, null, 2)),
  );
}

export const Panel: PanelDef = {
  id: "projet-workspace", label: "Workspace", icon: "workspaces", version: "1.0.0",
  description: "Initialisation et gestion des workspaces d'équipe",
  daemonRoutes: [
    { route: "team/init-workspace", methods: ["POST"], desc: "Initialiser" },
    { route: "team/create", methods: ["POST"], desc: "Créer équipe" },
  ],
  menu: [],
  declaration: () => "[projet-workspace] Workspace v1.0.0\n  Routes: team/init-workspace, team/create",
  component: () => React.createElement(WorkspacePanel),
};
