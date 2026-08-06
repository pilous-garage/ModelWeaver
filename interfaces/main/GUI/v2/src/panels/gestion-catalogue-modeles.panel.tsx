// gestion/catalogue-modeles — catalogue des modèles LLM. Migré de V1 (découplé).
// Routes : llm/models/list (poll 10s), providers/list.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  gestion-catalogue-modeles:
    titre: "Catalogue modèles"
    fournisseur: "Provider"
    modele: "Modèle"
    contexte: "Contexte"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  gestion-catalogue-modeles:
    titre: "Catalogue modèles"
    fournisseur: "Provider"
    modele: "Modèle"
    contexte: "Contexte"
    erreur: "Error"
`;


function CatalogueModelesPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'llm/models/list', {}, 10000,
    (res) => res?.result?.models ?? [], true,
  );
  const models = data ?? [];

  // regrouper par provider
  const byProvider = new Map<string, any[]>();
  for (const m of models) {
    const key = m.provider_ref ?? m.provider_name ?? '?';
    if (!byProvider.has(key)) byProvider.set(key, []);
    byProvider.get(key)!.push(m);
  }

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.gestion-catalogue-modeles.erreur') ?? 'Erreur'} : {error}</div>}
      {models.length === 0 && !error && <div style={{ color: '#64748b' }}>…</div>}
      {[...byProvider.entries()].map(([provider, ms]) => (
        <div key={provider} style={{ marginBottom: 8 }}>
          <div style={{ fontWeight: 600, color: '#93c5fd', marginBottom: 2 }}>{provider}</div>
          {ms.slice(0, 30).map((m: any) => (
            <div key={m.ref ?? m.name} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
              <span style={{ flex: 1 }}>{m.name ?? m.ref}</span>
              <span style={{ color: '#94a3b8' }}>{m.context_window_tokens ? `${m.context_window_tokens}` : ''}</span>
            </div>
          ))}
          {ms.length > 30 && <div style={{ color: '#64748b', fontSize: 11 }}>… +{ms.length - 30}</div>}
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'gestion-catalogue-modeles',
  labelKey: 'panels.gestion-catalogue-modeles.titre',
  iconKey: 'panels.gestion-catalogue-modeles.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['systeme', 'outils'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[gestion-catalogue-modeles] Catalogue modèles v1.0.0\n  routes: llm/models/list, providers/list',
  component: CatalogueModelesPanel,
};

export const langFr = LANG_FR;