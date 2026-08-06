// gestion/bundles — liste des bundles de panels. Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  gestion-bundles:
    titre: "Bundles"
    nom: "Bundle"
    nb: "panels"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  gestion-bundles:
    titre: "Bundles"
    nom: "Bundle"
    nb: "panels"
    erreur: "Error"
`;


function BundlesPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'catalogue/bundles/list', {},
    60000,
    (res) => unwrapResult(res).bundles ?? [],
  );
  const bundles = data ?? [];

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.gestion-bundles.erreur') ?? 'Erreur'} : {error}</div>}
      {bundles.length === 0 && !error && <div style={{ color: '#64748b' }}>…</div>}
      {bundles.map((b: any, i: number) => (
        <div key={b.name ?? i} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
          <span style={{ flex: 1 }}>{b.name ?? b.id}</span>
          <span style={{ color: '#64748b' }}>{Array.isArray(b.panels) ? `${b.panels.length} ${ctx.t?.('panels.gestion-bundles.nb') ?? 'panels'}` : ''}</span>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'gestion-bundles',
  labelKey: 'panels.gestion-bundles.titre',
  iconKey: 'panels.gestion-bundles.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['install', 'outils'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[gestion-bundles] Bundles v1.0.0\n  routes: catalogue/bundles/list',
  component: BundlesPanel,
};

export const langFr = LANG_FR;