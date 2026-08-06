// agents/team-members — moniteur d'une team (membres, activity/llm_used) +
// contrôle à chaud (stop/restart/remove/add). Routes : team/get, agent/metrics,
// agent/stop, agent/restart, team/add-member, team/remove-member.
// Param `team_name` (défaut "dev-chat").

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  agents-team-members:
    titre: "Équipe"
    membres: "Membres"
    leader: "Leader"
    statut: "Statut"
    taches: "Tâches"
    tokens: "Tokens"
    echecs: "Échecs"
    activite: "Activité"
    ajouter: "Ajouter"
    retirer: "Retirer"
    stop: "Stop"
    relancer: "Relancer"
    nom_agent: "Nom"
    role: "Rôle"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  agents-team-members:
    titre: "Team"
    membres: "Members"
    leader: "Leader"
    statut: "Status"
    taches: "Tasks"
    tokens: "Tokens"
    echecs: "Failures"
    activite: "Activity"
    ajouter: "Add"
    retirer: "Remove"
    stop: "Stop"
    relancer: "Restart"
    nom_agent: "Name"
    role: "Role"
    erreur: "Error"
`;

const STATUS_COLOR: Record<string, string> = {
  running: '#4ade80',
  INIT: '#f59e0b',
  STOPPED: '#94a3b8',
  failed: '#f87171',
};

function TeamMembersPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const teamName = params.team_name ?? 'dev-chat';
  const [busy, setBusy] = useState(false);
  const [addName, setAddName] = useState('');
  const [addRole, setAddRole] = useState('codeur');

  const team = usePoll<any>(
    ctx.api.post, 'team/get', { name: teamName }, 8000,
    (res) => unwrapResult(res).team ?? null, true,
  );
  const metrics = usePoll<any>(
    ctx.api.post, 'agent/metrics', {}, 8000,
    (res) => {
      const r = unwrapResult(res);
      const list = r.metrics ?? r.agents ?? [];
      const m: Record<string, any> = {};
      for (const x of list) if (x.agent_id != null) m[String(x.agent_id)] = x;
      return m;
    }, true,
  );

  const t = ctx.t?.bind ? ctx.t : (k: string) => k;

  const act = async (fn: () => Promise<any>) => {
    setBusy(true);
    try { await fn(); } catch { /* best-effort */ }
    setBusy(false);
  };
  const nameOf = (m: any) => m.agent_name ?? m.name ?? '';
  const fullName = (m: any) => `team:${teamName}/${nameOf(m)}`;

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>{teamName}</div>
      {team.error && <div style={{ color: '#f87171', marginBottom: 4 }}>{t('panels.agents-team-members.erreur')} : {team.error}</div>}
      {team.data && (
        <div style={{ color: '#94a3b8', marginBottom: 6 }}>
          {t('panels.agents-team-members.statut')} : {team.data.status ?? '—'} ·{' '}
          {t('panels.agents-team-members.leader')} : {team.data.team_leader?.agent_name ?? '—'} ·{' '}
          {t('panels.agents-team-members.membres')} : {team.data.member_count ?? 0}
        </div>
      )}

      {/* Boutons contrôle + add member */}
      <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
        <input value={addName} onChange={(e) => setAddName(e.target.value)} placeholder={t('panels.agents-team-members.nom_agent')}
          style={{ width: 90, fontSize: 11, padding: '2px 4px', background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)', border: '1px solid var(--mw-border, #334155)', borderRadius: 4 }} />
        <select value={addRole} onChange={(e) => setAddRole(e.target.value)}
          style={{ fontSize: 11, padding: '2px 4px', background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)', border: '1px solid var(--mw-border, #334155)', borderRadius: 4 }}>
          <option value="codeur">codeur</option>
          <option value="test_runner">testeur</option>
          <option value="relecteur">relecteur</option>
          <option value="explore">explore</option>
          <option value="architecte">architecte</option>
        </select>
        <button disabled={busy || !addName} onClick={() => act(async () => {
          await ctx.api.post('team/add-member', { name: teamName, agent_name: addName, role: addRole });
          setAddName('');
        })} style={{ fontSize: 11, padding: '2px 8px', cursor: 'pointer', background: 'var(--mw-accent, #3b82f6)', color: '#fff', border: 'none', borderRadius: 4 }}>
          {t('panels.agents-team-members.ajouter')}
        </button>
      </div>

      {/* Membres */}
      {(team.data?.members ?? []).map((m: any) => {
        const st = m.status ?? 'INIT';
        const met = metrics.data?.[String(m.agent_id)];
        return (
          <div key={m.agent_id} style={{ border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6, padding: '4px 6px', marginBottom: 4 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{nameOf(m)}</span>
              <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, background: (STATUS_COLOR[st] ?? '#64748b') + '22', color: STATUS_COLOR[st] ?? '#94a3b8' }}>{st}</span>
            </div>
            {met && (
              <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2 }}>
                {t('panels.agents-team-members.taches')} : {met.total_tasks ?? 0} ·{' '}
                {t('panels.agents-team-members.tokens')} : {met.total_tokens ?? 0} ·{' '}
                {t('panels.agents-team-members.echecs')} : {met.failed_tasks ?? 0}
              </div>
            )}
            <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
              <button disabled={busy} onClick={() => act(() => ctx.api.post('agent/stop', { name: fullName(m) }))}
                style={{ fontSize: 10, padding: '1px 6px', cursor: 'pointer', background: 'transparent', color: '#f87171', border: '1px solid #f87171', borderRadius: 4 }}>
                {t('panels.agents-team-members.stop')}
              </button>
              <button disabled={busy} onClick={() => act(() => ctx.api.post('agent/restart', { name: fullName(m) }))}
                style={{ fontSize: 10, padding: '1px 6px', cursor: 'pointer', background: 'transparent', color: '#4ade80', border: '1px solid #4ade80', borderRadius: 4 }}>
                {t('panels.agents-team-members.relancer')}
              </button>
              <button disabled={busy} onClick={() => act(() => ctx.api.post('team/remove-member', { name: teamName, agent_name: nameOf(m) }))}
                style={{ fontSize: 10, padding: '1px 6px', cursor: 'pointer', background: 'transparent', color: '#94a3b8', border: '1px solid #94a3b8', borderRadius: 4 }}>
                {t('panels.agents-team-members.retirer')}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-team-members',
  labelKey: 'panels.agents-team-members.titre',
  iconKey: 'panels.agents-team-members.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['agents'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-team-members] Équipe v1.0.0\n  routes: team/get, agent/metrics, agent/stop, agent/restart, team/add-member, team/remove-member',
  component: TeamMembersPanel,
};

export const langFr = LANG_FR;
