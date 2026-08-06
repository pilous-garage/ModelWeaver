// agents/equipe-leger — monitoring léger de la team en 3 colonnes de petits
// carrés (nom, LLM, tâche, status, step FSM, 5 dernières lignes LLM).
// Routes : agent/list-by-team (poll 4s), agent/get (conv lazy),
//          workspace/tasks/list, agent/metrics.
// Param `team_name` (défaut "dev-chat").

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  agents-equipe-leger:
    titre: "Équipe (léger)"
    erreur: "Erreur"
    vide: "Aucun membre"
`;

const LANG_EN = `
panels:
  agents-equipe-leger:
    titre: "Team (light)"
    erreur: "Error"
    vide: "No members"
`;


function TeamLightPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const teamName = params.team_name ?? 'dev-chat';
  const [convs, setConvs] = useState<Record<string, any[]>>({});
  const [opened, setOpened] = useState<string | null>(null);

  const agents = usePoll<any>(
    ctx.api.post, 'agent/list-by-team', {}, 4000,
    (res) => {
      const r = unwrapResult(res);
      return r.teams?.[teamName]?.agents ?? [];
    }, true,
  );
  const tasks = usePoll<any>(
    ctx.api.post, 'workspace/tasks/list', { workspace_id: 'mw-dev-chat', all: true }, 10000,
    (res) => unwrapResult(res).tasks ?? [], true,
  );

  const loadConv = async (a: any) => {
    const id = String(a.agent_id);
    if (convs[id] || opened === id) { setOpened(opened === id ? null : id); return; }
    try {
      const r = await ctx.api.post('agent/get', { agent_id: a.agent_id });
      const ag = unwrapResult(r).agent ?? {};
      let vars: any = {};
      try { vars = JSON.parse(ag.variables_json || '{}'); } catch { /* ignore */ }
      const msgs = vars.messages ?? [];
      setConvs((prev) => ({ ...prev, [id]: msgs }));
      setOpened(id);
    } catch { setConvs((prev) => ({ ...prev, [id]: [] })); setOpened(id); }
  };

  const members = (agents.data ?? []).map((a: any) => {
    const id = String(a.agent_id);
    const name = (a.name ?? '').replace(`team:${teamName}/`, '');
    const myTasks = (tasks.data ?? []).filter((tk: any) =>
      String(tk.assigned_to ?? '').includes(name) && tk.status !== 'done');
    return { ...a, id, name, task: myTasks[0]?.title ?? null, taskStatus: myTasks[0]?.status ?? null };
  });

  const statusColor = (a: any) => {
    if (a.running) return '#4ade80';
    if (a.current_step) return '#f59e0b';
    return '#475569';
  };

  const cell = (a: any) => {
    const col = statusColor(a);
    const lastMsgs = (convs[a.id] ?? []).slice(-5);
    return (
      <div key={a.id} style={{
        border: `1px solid ${col}55`, borderRadius: 8, padding: 5, margin: 4,
        background: '#0f172a', minHeight: 84, display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }} onClick={() => loadConv(a)}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: col, display: 'inline-block' }} />
          <span style={{ fontWeight: 700, fontSize: 12, color: 'var(--mw-fg, #e2e8f0)', flex: 1 }}>{a.name}</span>
          <span style={{ fontSize: 10, color: col }}>{a.running ? 'running' : (a.current_step ? 'waiting' : 'idle')}</span>
        </div>
        <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {a.current_step ? `step: ${a.current_step}` : '—'}
        </div>
        {a.task && (
          <div style={{ fontSize: 10, color: '#fbbf24', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={a.task}>
            {a.task}
          </div>
        )}
        {opened === a.id && (
          <div style={{ marginTop: 4, borderTop: '1px solid var(--mw-border, #1e293b)', paddingTop: 3, maxHeight: 90, overflowY: 'auto' }}>
            {lastMsgs.length === 0 && <div style={{ fontSize: 10, color: '#475569' }}>—</div>}
            {lastMsgs.map((m: any, i: number) => (
              <div key={i} style={{ fontSize: 10, marginBottom: 2 }}>
                <span style={{ color: m.role === 'user' ? '#60a5fa' : '#4ade80', fontWeight: 600 }}>{m.role}</span>
                <span style={{ color: '#cbd5e1' }}>: {String(m.content ?? '').slice(0, 60)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>{teamName}</div>
      {agents.error && <div style={{ color: '#f87171', fontSize: 11 }}>{ctx.t?.('panels.agents-equipe-leger.erreur') ?? 'Erreur'} : {agents.error}</div>}
      {(agents.data ?? []).length === 0 && !agents.error && (
        <div style={{ color: '#64748b' }}>{ctx.t?.('panels.agents-equipe-leger.vide') ?? 'Aucun membre'}</div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 4 }}>
        {(agents.data ?? []).map(cell)}
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-equipe-leger',
  labelKey: 'panels.agents-equipe-leger.titre',
  iconKey: 'panels.agents-equipe-leger.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['agents', 'monitoring'],
  paramsSchema: {
    team_name: { type: 'string', default: 'dev-chat' },
  },
  defaultParams: { team_name: 'dev-chat' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-equipe-leger] Équipe léger v1.0.0\n  3 colonnes de carrés (nom, LLM, tâche, status, step, conv)\n  routes: agent/list-by-team, agent/get, workspace/tasks/list',
  component: TeamLightPanel,
};

export const langFr = LANG_FR;