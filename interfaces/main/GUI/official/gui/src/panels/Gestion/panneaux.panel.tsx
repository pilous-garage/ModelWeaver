import React, { useState, useEffect } from 'react';
import { daemonPost } from '../../bridge.ts';
import { PANEL_REGISTRY } from '../index.ts';
import { getAllPanelStatus } from '../loader.ts';
import type { PanelDef } from '../../types.ts';

function PanelManager() {
  const [external, setExternal] = useState<any[]>([]);
  const [windows, setWindows] = useState<any[]>([]);
  const [templates, setTemplates] = useState<any>({});
  const [loading, setLoading] = useState(true);

  const refresh = () => {
    Promise.all([
      daemonPost('panels/index', {}).then(r => setExternal(r?.result?.panels || (r?.panels || []))),
      daemonPost('windows/list', {}).then(r => setWindows(r?.result?.windows || (r?.windows || []))),
      daemonPost('windows/templates', {}).then(r => setTemplates(r?.result?.templates || (r?.templates || {}))),
    ]).finally(() => setLoading(false));
  };

  useEffect(() => { refresh(); }, []);

  const status = getAllPanelStatus();
  const essential = Object.values(PANEL_REGISTRY).filter((p: any) => p.essential);

  if (loading) return React.createElement('div', { style: { color: '#94a3b8', padding: '1rem' } }, 'Chargement…');

  const card = (title: string, children: React.ReactNode) =>
    React.createElement('div', { style: { backgroundColor: '#1e293b', borderRadius: '0.4rem', border: '1px solid #334155', padding: '0.5rem', marginBottom: '0.5rem' } }, title, children);

  return React.createElement('div', { style: { padding: '0.5rem', fontSize: '0.78rem' } },
    React.createElement('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' } },
      React.createElement('h3', { style: { fontWeight: 600, margin: 0 } }, '🧩 Gestion des panneaux'),
      React.createElement('button', { onClick: refresh, style: { fontSize: '0.7rem', padding: '0.2rem 0.5rem' } }, 'Rafraîchir'),
    ),

    card(React.createElement('div', { style: { fontWeight: 600, marginBottom: '0.3rem' } }, `Essentiels (monolithe) — ${essential.length}`),
      essential.map((p: any) => React.createElement('div', { key: p.id, style: { display: 'flex', justifyContent: 'space-between', padding: '0.15rem 0' } },
        React.createElement('span', null, `${p.label} (${p.id})`),
        React.createElement('span', { style: { color: '#34d399', fontSize: '0.65rem' } }, `v${p.version}`),
      )),
    ),

    card(React.createElement('div', { style: { fontWeight: 600, marginBottom: '0.3rem' } }, `Externes (compilés) — ${external.length}`),
      external.map((p: any) => {
        const s = status[p.id];
        return React.createElement('div', { key: p.id, style: { display: 'flex', justifyContent: 'space-between', padding: '0.15rem 0', alignItems: 'center' } },
          React.createElement('span', null, `${p.label || p.id} (${p.id})`),
          React.createElement('div', { style: { display: 'flex', gap: '0.5rem', alignItems: 'center' } },
            React.createElement('span', { style: { color: s?.loaded ? '#34d399' : '#f87171', fontSize: '0.65rem' } }, s?.loaded ? 'chargé' : (s?.error ? 'erreur' : 'non chargé')),
            React.createElement('span', { style: { color: '#64748b', fontSize: '0.65rem' } }, `${((p.size || 0) / 1024).toFixed(1)} kB`),
          ),
        );
      }),
    ),

    card(React.createElement('div', { style: { fontWeight: 600, marginBottom: '0.3rem' } }, `Fenêtres ouvertes — ${windows.length}`),
      windows.map((w: any) => React.createElement('div', { key: w.window_id, style: { display: 'flex', justifyContent: 'space-between', padding: '0.15rem 0' } },
        React.createElement('span', null, `${w.title || w.window_id} (${w.template})`),
        React.createElement('span', { style: { color: '#64748b', fontSize: '0.65rem' } }, w.opened_at),
      )),
      Object.keys(templates).length > 0 && React.createElement('div', { style: { marginTop: '0.3rem', color: '#94a3b8', fontSize: '0.7rem' } },
        `Templates: ${Object.keys(templates).join(', ')}`),
    ),

    React.createElement('button', {
      onClick: () => daemonPost('windows/create', { template: 'dashboard' }).then(() => refresh()),
      style: { fontSize: '0.72rem', padding: '0.3rem 0.6rem', marginTop: '0.3rem' },
    }, '➕ Ouvrir une fenêtre Dashboard'),
  );
}

export const Panel: PanelDef = {
  id: "gestion-panneaux", label: "Gestion panneaux", icon: "extension", version: "1.0.0",
  essential: true,
  description: "Liste des panneaux (essentiels + externes), santé, fenêtres et templates",
  daemonRoutes: [
    { route: "panels/index", methods: ["GET"], desc: "Liste des panels externes" },
    { route: "panels/status", methods: ["GET"], desc: "Santé des panels" },
    { route: "windows/list", methods: ["GET"], desc: "Fenêtres ouvertes" },
    { route: "windows/templates", methods: ["GET"], desc: "Templates de fenêtres" },
    { route: "windows/create", methods: ["POST"], desc: "Ouvrir une fenêtre" },
  ],
  menu: [
    { menuPath: ["Gestion"], id: "panels:refresh", label: "Rafraîchir", action: "panels:refresh" },
  ],
  declaration: () => "[gestion-panneaux] Gestion panneaux v1.0.0\n  Panneaux essentiels + externes, santé, fenêtres",
  component: () => React.createElement(PanelManager),
};
