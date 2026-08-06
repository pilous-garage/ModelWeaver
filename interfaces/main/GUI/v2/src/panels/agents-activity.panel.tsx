// agents/activity — moniteur avancé des agents EN COURS : step FSM, activité,
// et conversation LLM (messages envoyés/répondus) pour l'agent sélectionné.
// Routes : agent/list (current_step, poll 4s), agent/get (variables = messages).

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  agents-activity:
    titre: "Activité agents"
    en_cours: "En cours"
    etape: "Étape"
    statut: "Statut"
    conversation: "Conversation"
    aucun: "Aucun agent en cours"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  agents-activity:
    titre: "Agent activity"
    en_cours: "Running"
    etape: "Step"
    statut: "Status"
    conversation: "Conversation"
    aucun: "No agents running"
    erreur: "Error"
`;

function AgentsActivityPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const teamFilter = params.team_name ?? 'dev-chat';
  const [sel, setSel] = useState<number | null>(null);
  const [conv, setConv] = useState<any[] | null>(null);

  const agents = usePoll<any>(ctx.api.post, 'agent/list', {}, 4000,
    (res) => unwrapResult(res).agents ?? [], true);

  const running = (agents.data ?? []).filter((a: any) => a.running && (a.name ?? '').includes(`team:${teamFilter}`));

  const loadConv = async (agentId: number) => {
    setSel(agentId);
    try {
      const r = await ctx.api.post('agent/get', { agent_id: agentId });
      const agent = unwrapResult(r).agent ?? {};
      let vars: any = {};
      try { vars = JSON.parse(agent.variables_json || '{}'); } catch { /* ignore */ }
      setConv(vars.messages ?? []);
    } catch {
      setConv([]);
    }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{ctx.t?.('panels.agents-activity.en_cours') ?? 'En cours'} ({running.length})</div>
      {agents.error && <div style={{ color: '#f87171' }}>{agents.error}</div>}
      {running.length === 0 && !agents.error && (
        <div style={{ color: '#64748b' }}>{ctx.t?.('panels.agents-activity.aucun') ?? 'Aucun agent en cours'}</div>
      )}
      {running.map((a: any) => (
        <div key={a.agent_id} onClick={() => loadConv(a.agent_id)}
          style={{
            border: `1px solid ${sel === a.agent_id ? 'var(--mw-accent, #3b82f6)' : 'var(--mw-border, #1e293b)'}`,
            borderRadius: 6, padding: '4px 6px', marginBottom: 4, cursor: 'pointer',
            background: sel === a.agent_id ? 'var(--mw-accent, #3b82f6)18' : 'transparent',
          }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{a.name}</span>
            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, background: '#4ade8022', color: '#4ade80' }}>{a.status}</span>
          </div>
          <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2, fontFamily: 'monospace' }}>
            {ctx.t?.('panels.agents-activity.etape') ?? 'Étape'} : {a.current_step ?? '—'} · #{a.thread_id ?? '—'}
          </div>
        </div>
      ))}

      {sel !== null && conv !== null && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{ctx.t?.('panels.agents-activity.conversation') ?? 'Conversation'}</div>
          {conv.length === 0 && <div style={{ color: '#64748b' }}>—</div>}
          {conv.slice(-12).map((m: any, i: number) => (
            <div key={i} style={{ marginBottom: 3 }}>
              <div style={{ color: m.role === 'user' ? '#3b82f6' : '#4ade80', fontWeight: 600 }}>{m.role}</div>
              <div style={{ color: 'var(--mw-fg-dim, #cbd5e1)', whiteSpace: 'pre-wrap', fontSize: 11 }}>{String(m.content ?? '').slice(0, 300)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-activity',
  labelKey: 'panels.agents-activity.titre',
  iconKey: 'panels.agents-activity.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['agents', 'monitoring'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-activity] Activité agents v1.0.0\n  routes: agent/list, agent/get',
  component: AgentsActivityPanel,
};

export const langFr = LANG_FR;
