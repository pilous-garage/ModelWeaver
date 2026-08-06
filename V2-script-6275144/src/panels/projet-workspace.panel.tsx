// projet/workspace — initialise un workspace team. Migré de V1.

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';

const LANG_FR = `
panels:
  projet-workspace:
    titre: "Workspace"
    nom: "Nom du projet"
    init: "Initialiser le workspace"
    ok: "Workspace initialisé"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  projet-workspace:
    titre: "Workspace"
    nom: "Project name"
    init: "Initialize workspace"
    ok: "Workspace initialisé"
    erreur: "Error"
`;


function WorkspacePanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const [teamName, setTeamName] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const init = async () => {
    if (!teamName.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await ctx.api.post('team/init-workspace', { name: teamName.trim() });
      setResult(res);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          value={teamName}
          onChange={(e) => setTeamName(e.target.value)}
          placeholder={ctx.t?.('panels.projet-workspace.nom') ?? 'Nom du projet'}
          style={{ flex: 1, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '6px 8px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 12 }}
        />
        <button className="mw-btn" onClick={init} disabled={loading || !teamName.trim()}>
          {ctx.t?.('panels.projet-workspace.init') ?? 'Initialiser le workspace'}
        </button>
      </div>
      {error && <div style={{ color: '#f87171', marginTop: 8 }}>{ctx.t?.('panels.projet-workspace.erreur') ?? 'Erreur'} : {error}</div>}
      {result && <pre style={{ marginTop: 8, background: 'var(--mw-bg, #0f172a)', padding: 8, borderRadius: 6, overflow: 'auto', fontSize: 11 }}>{JSON.stringify(result, null, 2)}</pre>}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'projet-workspace',
  labelKey: 'panels.projet-workspace.titre',
  iconKey: 'panels.projet-workspace.titre',
  version: '1.0.0',
  essential: false,
  paramsSchema: {
    name: { type: 'string', description: 'Nom du workspace' },
  },
  defaultParams: { name: '' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[projet-workspace] Workspace v1.0.0\n  routes: team/init-workspace',
  component: WorkspacePanel,
};

export const langFr = LANG_FR;