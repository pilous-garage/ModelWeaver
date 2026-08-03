import React, { useState, useEffect } from 'react';
import { daemonPost } from '../../bridge.ts';
import type { PanelDef } from '../../types.ts';

function BundlesPanel() {
  const [bundles, setBundles] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    daemonPost('catalogue/bundles/list', {}).then(r => { setBundles(r?.result?.bundles || []); setLoading(false); }).catch(() => setLoading(false));
  }, []);
  if (loading) return React.createElement('div', { style: { color: '#94a3b8', padding: '1rem' } }, 'Chargement…');
  return React.createElement('div', { style: { padding: '0.5rem', fontSize: '0.78rem' } },
    React.createElement('h3', { style: { fontWeight: 600, marginBottom: '0.5rem' } }, '📦 Bundles'),
    ...bundles.map((b: any) => React.createElement('div', {
      key: b.name, style: { backgroundColor: '#1e293b', borderRadius: '0.3rem', border: '1px solid #334155', padding: '0.4rem 0.6rem', marginBottom: '0.3rem' }
    },
      React.createElement('div', { style: { fontWeight: 600 } }, b.name),
      React.createElement('div', { style: { fontSize: '0.65rem', color: '#64748b' } }, b.description || ''),
      React.createElement('div', { style: { fontSize: '0.6rem', color: '#475569', marginTop: '0.15rem' } }, `${b.skill_count} skills · ${b.permission_count} permissions`),
    )),
  );
}

export const Panel: PanelDef = {
  id: "gestion-bundles", label: "Bundles", icon: "inventory_2", version: "1.0.0",
  description: "Gestion des bundles de skills",
  daemonRoutes: [
    { route: "catalogue/bundles/list", methods: ["GET"], desc: "Liste des bundles" },
    { route: "catalogue/bundles/get", methods: ["GET"], desc: "Détail" },
    { route: "catalogue/bundles/save", methods: ["POST"], desc: "Sauvegarder" },
  ],
  menu: [{ menuPath: ["Fichier"], id: "bundles:new", label: "Nouveau bundle", action: "panel:bundles:new" }],
  declaration: () => "[gestion-bundles] Bundles v1.0.0\n  Routes: catalogue/bundles/*\n  Menu: Fichier > Nouveau bundle",
  component: () => React.createElement(BundlesPanel),
};
