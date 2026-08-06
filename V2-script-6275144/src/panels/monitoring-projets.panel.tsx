// monitoring/projets — liste des teams/workspaces (poll 10s). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  monitoring-projets:
    titre: "Projets"
    nom: "Projet"
    statut: "Statut"
    leaders: "Responsables"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  monitoring-projets:
    titre: "Projects"
    nom: "Projet"
    statut: "Status"
    leaders: "Leaders"
    erreur: "Error"
`;


function ProjetsPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'team/list', {},
    10000,
    (res) => unwrapResult(res).teams ?? [],
  );

  const teams = data ?? [];
  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.monitoring-projets.erreur') ?? 'Erreur'} : {error}</div>}
      {teams.length === 0 && !error && <div style={{ color: '#64748b' }}>Aucun projet</div>}
      {teams.map((team: any) => (
        <div key={team.name ?? team.id} style={{ border: '1px solid var(--mw-border, #1e293b)', borderRadius: 8, padding: 8, marginBottom: 8 }}>
          <div style={{ fontWeight: 600 }}>{team.name}</div>
          <div style={{ color: '#94a3b8', marginTop: 2 }}>
            {ctx.t?.('panels.monitoring-projets.statut') ?? 'Statut'} : {team.status ?? '—'} ·{' '}
            {ctx.t?.('panels.monitoring-projets.leaders') ?? 'Responsables'} : {team.team_leader ?? '—'}
          </div>
          {team.members && team.members.length > 0 && (
            <div style={{ color: '#64748b', marginTop: 2 }}>{team.members.map((m: any) => m.name ?? m).join(', ')}</div>
          )}
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'monitoring-projets',
  labelKey: 'panels.monitoring-projets.titre',
  iconKey: 'panels.monitoring-projets.titre',
  version: '1.0.0',
  essential: false,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[monitoring-projets] Projets v1.0.0\n  routes: team/list',
  component: ProjetsPanel,
};

export const langFr = LANG_FR;