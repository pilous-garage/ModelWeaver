// agents/equipe — composition des équipes (membres, leader). Migré de V1.
// Covers `agents-composition-equipe` et `projet-equipes`.
// Routes : team/list (poll 10s), team/add-member, team/set-leader, capabilities.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  agents-equipe:
    titre: "Équipes"
    membres: "Membres"
    leader: "Leader"
    statut: "Statut"
    topologie: "Topologie"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  agents-equipe:
    titre: "Équipes"
    membres: "Membres"
    leader: "Leader"
    statut: "Status"
    topologie: "Topologie"
    erreur: "Error"
`;


function EquipePanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'team/list', {}, 10000,
    (res) => res?.result?.teams ?? [], true,
  );
  const teams = data ?? [];

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.agents-equipe.erreur') ?? 'Erreur'} : {error}</div>}
      {teams.length === 0 && !error && <div style={{ color: '#64748b' }}>Aucune équipe</div>}
      {teams.map((t: any) => (
        <div key={t.name} style={{ border: '1px solid var(--mw-border, #1e293b)', borderRadius: 8, padding: 8, marginBottom: 8 }}>
          <div style={{ fontWeight: 600 }}>{t.name} <span style={{ fontSize: 10, color: '#64748b' }}>· {t.topology ?? ''}</span></div>
          <div style={{ color: '#94a3b8', marginTop: 2 }}>
            {ctx.t?.('panels.agents-equipe.statut') ?? 'Statut'} : {t.status ?? '—'} ·{' '}
            {ctx.t?.('panels.agents-equipe.leader') ?? 'Leader'} : {t.team_leader?.agent_name ?? '—'}
          </div>
          {t.members && t.members.length > 0 && (
            <div style={{ marginTop: 4 }}>
              <div style={{ color: '#64748b', fontSize: 11 }}>{ctx.t?.('panels.agents-equipe.membres') ?? 'Membres'} ({t.members.length})</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 2 }}>
                {t.members.map((m: any) => (
                  <span key={m.agent_name} style={{ fontSize: 10, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 4, padding: '1px 6px', color: m.status === 'running' ? '#4ade80' : '#94a3b8' }}>
                    {m.agent_name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-equipe',
  labelKey: 'panels.agents-equipe.titre',
  iconKey: 'panels.agents-equipe.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['agents'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-equipe] Équipes v1.0.0\n  routes: team/list, team/add-member, team/set-leader, capabilities',
  component: EquipePanel,
};

export const langFr = LANG_FR;