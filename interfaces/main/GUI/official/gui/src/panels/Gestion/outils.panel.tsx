import React, { useState, useEffect } from 'react';
import { daemonPost } from '../../bridge.ts';
import type { PanelDef } from '../../types.ts';

function ToolsPanel() {
  const [tools, setTools] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    daemonPost('catalogue/tools/list', {}).then(r => { setTools(r.tools || []); setLoading(false); }).catch(() => setLoading(false));
  }, []);
  if (loading) return React.createElement('div', { style: { color: '#94a3b8', padding: '1rem' } }, 'Chargement…');
  return React.createElement('div', { style: { padding: '0.5rem', fontSize: '0.78rem' } },
    React.createElement('h3', { style: { fontWeight: 600, marginBottom: '0.5rem' } }, '🔧 Outils Registry'),
    ...tools.map((t: any) => React.createElement('div', {
      key: t.name, style: { backgroundColor: '#1e293b', borderRadius: '0.3rem', border: '1px solid #334155', padding: '0.4rem 0.6rem', marginBottom: '0.3rem' }
    },
      React.createElement('div', { style: { fontWeight: 600 } }, t.name),
      React.createElement('div', { style: { fontSize: '0.65rem', color: '#64748b' } }, t.description?.substring(0, 80) || ''),
      React.createElement('div', { style: { fontSize: '0.6rem', color: '#475569', marginTop: '0.15rem' } }, `impl: ${t.implementation || '—'}`),
    )),
  );
}

export const Panel: PanelDef = {
  id: "gestion-outils", label: "Outils Registry", icon: "build", version: "1.0.0",
  description: "Outils YAML auto-découverts (delegate, edit, grep…)",
  daemonRoutes: [
    { route: "catalogue/tools/list", methods: ["GET"], desc: "Liste des outils" },
    { route: "catalogue/tools/get", methods: ["GET"], desc: "Détail" },
  ],
  menu: [],
  declaration: () => "[gestion-outils] Outils Registry v1.0.0\n  Routes: catalogue/tools/list, catalogue/tools/get",
  component: () => React.createElement(ToolsPanel),
};
