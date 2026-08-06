// agents/team-members — moniteur d'une team : membres running/idle/waiting,
// tâche en cours (workspace/tasks/list), LLM assigné (agent/get config),
// step FSM (agent/list-by-team) + contrôle à chaud (stop/restart/remove/add).
// Routes : team/get, agent/list-by-team, agent/get, agent/metrics, agent/stop,
//          agent/restart, team/add-member, team/remove-member, workspace/tasks/list.
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
    tache_cours: "Tâche en cours"
    llm: "LLM"
    step: "Étape"
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
    tache_cours: "Current task"
    llm: "LLM"
    step: "Step"
`;

const STATUS_COLOR: Record<string, string> = {
  running: '#4ade80',
  idle: '#94a3b8',
  waiting: '#f59e0b',
  INIT: '#f59e0b',
  STOPPED: '#94a3b8',
  failed: '#f87171',
};

function TeamMembersPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const teamName = params.team_name ?? 'dev-chat';
  const [busy, setBusy] = useState(false);
  const [addName, setAddName] = useState('');
  const [addRole, setAddRole] = useState('codeur');
  const [llms, setLlms] = useState<Record<string, string>>({});

  const team = usePoll<any>(
    ctx.api.post, 'team/get', { name: teamName }, 8000,
    (res) => unwrapResult(res).team ?? null, true,
  );
  const byTeam = usePoll<any>(
    ctx.api.post, 'agent/list-by-team', {}, 8000,
    (res) => {
      const r = unwrapResult(res);
      const teamData = r.teams?.[teamName];
      const m: Record<string, any> = {};
      for (const a of teamData?.agents ?? []) m[String(a.agent_id)] = a;
      return m;
    }, true,
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
  const tasks = usePoll<any>(
    ctx.api.post, 'workspace/tasks/list', { workspace_id: 'mw-dev-chat', all: true }, 10000,
    (res) => unwrapResult(res).tasks ?? [], true,
  );

  const t = ctx.t?.bind ? ctx.t : (k: string) => k;

  const act = async (fn: () => Promise<any>) => {
    setBusy(true);
    try { await fn(); } catch { /* best-effort */ }
    setBusy(false);
  };
  const nameOf = (m: any) => m.agent_name ?? m.name ?? '';
  const fullName = (m: any) => `team:${teamName}/${nameOf(m)}`;

  const loadLlm = async (agentId: number) => {
    try {
      const r = await ctx.api.post('agent/get', { agent_id: agentId });
      const a = unwrapResult(r).agent ?? {};
      let cfg: any = {};
      try { cfg = JSON.parse(a.config_json || '{}'); } catch { /* ignore */ }
      const wf = cfg.workflow?.steps?.find((s: any) => s.provider_ref || s.model_ref);
      const provider = wf?.provider_ref ?? cfg.provider_ref ?? '';
      const model = wf?.model_ref ?? cfg.model_ref ?? '';
      const label = [provider, model].filter(Boolean).join('/') || '—';
      setLlms((prev) => ({ ...prev, [String(agentId)]: label }));
    } catch { /* best-effort */ }
  };

  const members = (team.data?.members ?? []).map((m: any) => {
    const id = String(m.agent_id);
    const bt = byTeam.data?.[id];
    const met = metrics.data?.[id];
    // Statut dérivé : running si agent_runtime, waiting si current_step posé,
    // idle sinon.
    const running = bt?.running ?? false;
    const step = bt?.current_step ?? m.current_step ?? null;
    const status = running ? 'running' : (step ? 'waiting' : 'idle');
    const myTasks = (tasks.data ?? []).filter((tk: any) =>
      String(tk.assigned_to ?? '').includes(nameOf(m)) && tk.status !== 'done');
    const currentTask = myTasks[0]?.title ?? null;
    return { ...m, id, bt, met, running, step, status, currentTask };
  });

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>{teamName}</div>
      {team.error && <div style={{ color: '#f87171', marginBottom: 4 }}>{t('panels.agents-team-members.erreur')} : {team.error}</div>}
      {team.data && (
        <div style={{ color: '#94a3b8', marginBottom: 6 }}>
          {t('panels.agents-team-members.statut')} : {team.data.status ?? '—'} ·{' '}
          {t('panels.agents-team-members.leader')} : {team.data.team_leader?.agent_name ?? '—'} ·{' '}
          {t('panels.agents-team-members.membres')} : {team.data.member_count ?? members.length}
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
      {members.map((m: any) => {
        const st = m.status ?? 'INIT';
        return (
          <div key={m.agent_id} style={{ border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6, padding: '4px 6px', marginBottom: 4 }}
            onMouseEnter={() => { if (!llms[m.id]) loadLlm(m.agent_id); }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{nameOf(m)}</span>
              <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, background: (STATUS_COLOR[st] ?? '#64748b') + '22', color: STATUS_COLOR[st] ?? '#94a3b8' }}>{st}</span>
            </div>
            {m.step && (
              <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2, fontFamily: 'monospace' }}>
                {t('panels.agents-team-members.step') ?? 'Étape'} : {m.step}
              </div>
            )}
            {m.currentTask && (
              <div style={{ color: '#fbbf24', fontSize: 11, marginTop: 2 }}>
                {t('panels.agents-team-members.tache_cours') ?? 'Tâche en cours'} : {m.currentTask}
              </div>
            )}
            <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2 }}>
              {t('panels.agents-team-members.llm') ?? 'LLM'} : {llms[m.id] ?? '…'} ·{' '}
              {t('panels.agents-team-members.taches')} : {m.met?.total_tasks ?? 0} ·{' '}
              {t('panels.agents-team-members.tokens')} : {m.met?.total_tokens ?? 0} ·{' '}
              {t('panels.agents-team-members.echecs')} : {m.met?.failed_tasks ?? 0}
            </div>
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
  version: '1.1.0',
  essential: false,
  bundles: ['agents'],
  paramsSchema: {
    team_name: { type: 'string', default: 'dev-chat' },
  },
  defaultParams: { team_name: 'dev-chat' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-team-members] Équipe v1.1.0\n  membres (running/idle/waiting), tâche en cours, LLM assigné, step FSM\n  routes: team/get, agent/list-by-team, agent/get, agent/metrics, agent/stop, agent/restart, team/add-member, team/remove-member, workspace/tasks/list',
  component: TeamMembersPanel,
};

export const langFr = LANG_FR;