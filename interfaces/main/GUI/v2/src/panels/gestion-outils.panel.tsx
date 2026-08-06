// gestion/outils — catalogue des outils (one-shot au mount). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  gestion-outils:
    titre: "Outils Registry"
    nom: "Outil"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  gestion-outils:
    titre: "Tools Registry"
    nom: "Tool"
    erreur: "Error"
`;


function OutilsPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'catalogue/tools/list', {},
    60000,
    (res) => unwrapResult(res).tools ?? [],
  );
  const tools = data ?? [];

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.gestion-outils.erreur') ?? 'Erreur'} : {error}</div>}
      {tools.length === 0 && !error && <div style={{ color: '#64748b' }}>…</div>}
      {tools.map((tool: any, i: number) => (
        <div key={tool.id ?? i} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
          <span style={{ flex: 1 }}>{tool.name ?? tool.id}</span>
          <span style={{ color: '#64748b' }}>{tool.version ?? ''}</span>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'gestion-outils',
  labelKey: 'panels.gestion-outils.titre',
  iconKey: 'panels.gestion-outils.titre',
  version: '1.0.0',
  essential: false,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[gestion-outils] Outils Registry v1.0.0\n  routes: catalogue/tools/list',
  component: OutilsPanel,
};

export const langFr = LANG_FR;