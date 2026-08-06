// agents/lanceur — lancer un agent avec une requête. Migré de V1.
// Route : agent/launch (body: role, request, provider_ref?, model_ref?).

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';

const LANG_FR = `
panels:
  agents-lanceur:
    titre: "Lanceur"
    role: "Rôle"
    requete: "Requête"
    lancer: "Lancer"
    resultat: "Résultat"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  agents-lanceur:
    titre: "Lanceur"
    role: "Rôle"
    requete: "Requête"
    lancer: "Lancer"
    resultat: "Résultat"
    erreur: "Error"
`;


function LanceurPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [role, setRole] = useState(params.role ?? 'assistant');
  const [request, setRequest] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const launch = async () => {
    if (!request.trim() || loading) return;
    setLoading(true); setError(null); setResult(null);
    try {
      const res = await ctx.api.post('agent/launch', { role, request: request.trim() });
      setResult(res?.result ?? res);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
        <span style={{ color: '#64748b' }}>{ctx.t?.('panels.agents-lanceur.role') ?? 'Rôle'}</span>
        <input value={role} onChange={(e) => setRole(e.target.value)}
          style={{ width: 120, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '4px 8px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 12 }} />
      </div>
      <textarea value={request} onChange={(e) => setRequest(e.target.value)} placeholder={ctx.t?.('panels.agents-lanceur.requete') ?? 'Requête'}
        rows={4} style={{ width: '100%', background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: 6, color: 'var(--mw-fg, #e2e8f0)', fontSize: 12, boxSizing: 'border-box' }} />
      <button className="mw-btn" onClick={launch} disabled={loading || !request.trim()} style={{ marginTop: 6 }}>
        {ctx.t?.('panels.agents-lanceur.lancer') ?? 'Lancer'}
      </button>
      {error && <div style={{ color: '#f87171', marginTop: 8 }}>{ctx.t?.('panels.agents-lanceur.erreur') ?? 'Erreur'} : {error}</div>}
      {result && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{ctx.t?.('panels.agents-lanceur.resultat') ?? 'Résultat'}</div>
          <pre style={{ background: 'var(--mw-bg, #0f172a)', padding: 8, borderRadius: 6, overflow: 'auto', fontSize: 11 }}>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-lanceur',
  labelKey: 'panels.agents-lanceur.titre',
  iconKey: 'panels.agents-lanceur.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['agents'],
  paramsSchema: {
    role: { type: 'string', default: 'assistant', description: 'Rôle de l\'agent' },
  },
  defaultParams: { role: 'assistant' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-lanceur] Lanceur v1.0.0\n  routes: agent/launch',
  component: LanceurPanel,
};

export const langFr = LANG_FR;