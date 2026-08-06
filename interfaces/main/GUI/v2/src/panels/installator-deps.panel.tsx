// installator/deps — dépendances requises/recommandées. Migré de V1 (découplé).
// Routes : deps/check_manifest (poll 60s), deps/install_target.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  installator-deps:
    titre: "Dépendances"
    requises: "Requis"
    recommande: "Recommandé"
    installer: "Installer"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  installator-deps:
    titre: "Dépendances"
    requises: "Requis"
    recommande: "Recommandé"
    installer: "Installer"
    erreur: "Error"
`;


function DepsPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'deps/check_manifest', {}, 60000,
    (res) => res?.result ?? {}, true,
  );
  const deps = data?.dependencies ?? [];
  const required = deps.filter((d: any) => d.required);
  const recommended = deps.filter((d: any) => !d.required);

  const install = async (includeOptional: boolean) => {
    try { await ctx.api.post('deps/install_target', { include_optional: includeOptional }); reload(); } catch { /* best-effort */ }
  };

  const row = (d: any) => (
    <div key={d.name} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', fontSize: 12 }}>
      <span style={{ flex: 1 }}>{d.name}</span>
      <span style={{ color: d.installed ? '#4ade80' : '#f59e0b' }}>{d.installed ? '✓' : d.safe ? '·' : '✗'}</span>
      <span style={{ color: '#64748b', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.description ?? ''}</span>
    </div>
  );

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6, fontSize: 12 }}>{ctx.t?.('panels.installator-deps.erreur') ?? 'Erreur'} : {error}</div>}
      <div style={{ fontWeight: 600, margin: '4px 0' }}>{ctx.t?.('panels.installator-deps.requises') ?? 'Requis'}</div>
      {required.map(row)}
      {recommended.length > 0 && (
        <>
          <div style={{ fontWeight: 600, margin: '8px 0 4px' }}>{ctx.t?.('panels.installator-deps.recommande') ?? 'Recommandé'}</div>
          {recommended.map(row)}
        </>
      )}
      <button className="mw-btn" onClick={() => install(false)} style={{ marginTop: 8, fontSize: 12 }}>
        {ctx.t?.('panels.installator-deps.installer') ?? 'Installer'}
      </button>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'installator-deps',
  labelKey: 'panels.installator-deps.titre',
  iconKey: 'panels.installator-deps.titre',
  version: '1.0.0',
  essential: true,
  bundles: ['install'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[installator-deps] Dépendances v1.0.0\n  routes: deps/check_manifest, deps/install_target',
  component: DepsPanel,
};

export const langFr = LANG_FR;