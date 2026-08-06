// gestion/cles-api — clés API des providers. Migré de V1.
// Routes : keys/list (poll 5s), keys/delete, keys/set_lock.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  gestion-cles-api:
    titre: "Clés API"
    provider: "Provider"
    etat: "État"
    verrou: "Verrou"
    suppr: "Supprimer"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  gestion-cles-api:
    titre: "API keys"
    provider: "Provider"
    etat: "State"
    verrou: "Lock"
    suppr: "Delete"
    erreur: "Error"
`;


function ClesApiPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'keys/list', {}, 5000,
    (res) => unwrapResult(res).keys ?? [],
    true,
  );
  const keys = data ?? [];

  const remove = async (ref: string) => {
    try { await ctx.api.post('keys/delete', { ref }); reload(); } catch { /* best-effort */ }
  };
  const toggleLock = async (ref: string, locked: boolean) => {
    try { await ctx.api.post('keys/set_lock', { ref, locked: !locked }); reload(); } catch { /* best-effort */ }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.gestion-cles-api.erreur') ?? 'Erreur'} : {error}</div>}
      {keys.length === 0 && !error && <div style={{ color: '#64748b' }}>Aucune clé</div>}
      {keys.map((k: any) => (
        <div key={k.ref ?? k.provider ?? k.id} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{k.provider ?? k.ref ?? k.id}</span>
          <span style={{ color: '#94a3b8' }}>{k.key_display ?? '****'}</span>
          <span style={{ color: k.locked ? '#f59e0b' : '#4ade80' }}>{k.locked ? '🔒' : '🔓'}</span>
          <button className="mw-btn" onClick={() => toggleLock(k.ref ?? k.id, !!k.locked)} style={{ fontSize: 11 }}>
            {ctx.t?.('panels.gestion-cles-api.verrou') ?? 'Verrou'}
          </button>
          <button className="mw-btn" onClick={() => remove(k.ref ?? k.id)} style={{ fontSize: 11 }}>
            {ctx.t?.('panels.gestion-cles-api.suppr') ?? 'Supprimer'}
          </button>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'gestion-cles-api',
  labelKey: 'panels.gestion-cles-api.titre',
  iconKey: 'panels.gestion-cles-api.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['systeme', 'outils'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[gestion-cles-api] Clés API v1.0.0\n  routes: keys/list, keys/delete, keys/set_lock',
  component: ClesApiPanel,
};

export const langFr = LANG_FR;