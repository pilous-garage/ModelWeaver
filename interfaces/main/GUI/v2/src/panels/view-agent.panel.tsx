// view-agent — moniteur d'UN SEUL agent : données, conversation LLM, FSM log.
// Layout :
//   barre du haut  : sélecteur d'agent (par team + hors-team)
//   3 colonnes     : [gauche] infos agent (variables, tâches, métriques)
//                    [milieu] conversation LLM (messages foldables)
//                    [droite] FSM log / graph (switch)
// Routes : agent/list-by-team, agent/get, agent/logs, agent/graph,
//          agent/metrics, workspace/tasks/list.

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';
import { CustomSelect } from '../components/CustomSelect.tsx';

const LANG_FR = `
panels:
  view-agent:
    titre: "Vue agent"
    select_agent: "Agent"
    toutes_equipes: "Toutes les équipes"
    hors_team: "Hors team"
    infos: "Agent"
    variables: "Variables"
    taches: "Tâches"
    aucune_tache: "Aucune tâche"
    conversation: "Conversation LLM"
    envoye: "Envoyé"
    fsm_log: "Log FSM"
    graph: "Graph"
    vide: "Sélectionne un agent"
    error: "Erreur"
    details: "Détails"
    replier: "Replier"
    content: "Contenu"
    tools: "Outils"
    usage: "Usage"
    status: "Statut"
    role: "Rôle"
    occupation: "Occupation"
    activite: "Activité"
    derniere_activite: "Dernière activité"
    tokens: "Tokens"
    requetes: "Requêtes"
    echecs: "Échecs"
`;

const LANG_EN = `
panels:
  view-agent:
    titre: "View agent"
    select_agent: "Agent"
    toutes_equipes: "All teams"
    hors_team: "No team"
    infos: "Agent"
    variables: "Variables"
    taches: "Tasks"
    aucune_tache: "No tasks"
    conversation: "LLM conversation"
    envoye: "Sent"
    fsm_log: "FSM log"
    graph: "Graph"
    vide: "Select an agent"
    error: "Error"
    details: "Details"
    replier: "Collapse"
    content: "Content"
    tools: "Tools"
    usage: "Usage"
    status: "Status"
    role: "Role"
    occupation: "Occupation"
    activite: "Activity"
    derniere_activite: "Last activity"
    tokens: "Tokens"
    requetes: "Requests"
    echecs: "Errors"
`;

const mono: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 11,
};

interface AgentItem { team: string; id: number; name: string }

const label = (t: any, k: string, fb: string) => t(`panels.view-agent.${k}`) ?? fb;

function ViewAgentPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const t = ctx.t?.bind ? ctx.t : (k: string) => k;
  const [selTeam, setSelTeam] = useState<string>(params.team_name ?? 'dev-chat');
  const [selAgent, setSelAgent] = useState<number | null>(null);
  const [view, setView] = useState<'fsm' | 'graph'>('fsm');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // ── Agents groupés par team (avec hors-team) ──
  const byTeam = usePoll<any>(ctx.api.post, 'agent/list-by-team', {}, 5000,
    (res) => {
      const r = unwrapResult(res);
      const teams = r.teams ?? {};
      const list: { team: string; id: number; name: string }[] = [];
      const seen = new Set<number>();
      for (const [team, info] of Object.entries<any>(teams)) {
        for (const a of info.agents ?? []) {
          if (seen.has(a.agent_id)) continue;
          seen.add(a.agent_id);
          list.push({ team, id: a.agent_id, name: a.name ?? `#${a.agent_id}` });
        }
      }
      for (const a of r.standalone ?? []) {
        if (seen.has(a.agent_id)) continue;
        seen.add(a.agent_id);
        list.push({ team: 'hors-team', id: a.agent_id, name: a.name ?? `#${a.agent_id}` });
      }
      return list;
    }, true);

  const agents: AgentItem[] = byTeam.data ?? [];
  const teams = Array.from(new Set(['toutes', ...agents.map((a: AgentItem) => a.team)]));
  const filtered = selTeam === 'toutes'
    ? agents
    : agents.filter((a: AgentItem) => a.team === selTeam);

  // ── Données de l'agent sélectionné ──
  const agentBody = selAgent != null ? { agent_id: selAgent } : { agent_id: -1 };
  const agentData = usePoll<any>(ctx.api.post, 'agent/get', agentBody, 5000,
    (res) => unwrapResult(res).agent ?? null, true);
  const logs = usePoll<any>(ctx.api.post, 'agent/logs', agentBody, 3000,
    (res) => unwrapResult(res) ?? { fsm: [], conversation: [] }, true);
  const graph = usePoll<any>(ctx.api.post, 'agent/graph', agentBody, 5000,
    (res) => unwrapResult(res) ?? { graph: [] }, true);
  const metrics = usePoll<any>(ctx.api.post, 'agent/metrics', agentBody, 8000,
    (res) => {
      const r = unwrapResult(res);
      return r.metrics ?? r;
    }, true);

  // ── Tâches attribuées à l'agent (tous workspaces) ──
  const tasks = usePoll<any>(ctx.api.post, 'workspace/tasks/list',
    { workspace_id: 'mw-dev-chat', all: true }, 8000,
    (res) => (unwrapResult(res).tasks ?? []).filter((tk: any) =>
      selAgent != null && String(tk.assigned_to ?? '').includes(String(selAgent))),
    true);

  const agent = agentData.data;
  const conv = logs.data?.conversation ?? [];
  const fsmRuns = logs.data?.fsm ?? [];
  const graphData = graph.data?.graph ?? [];
  const met = metrics.data ?? {};

  // Variables (json) de l'agent.
  let variables: any = {};
  try { variables = JSON.parse(agent?.variables_json ?? '{}'); } catch { /* ignore */ }

  const toggle = (i: number) => {
    setExpanded((prev) => {
      const s = new Set(prev);
      if (s.has(i)) s.delete(i); else s.add(i);
      return s;
    });
  };

  // Dernière ligne FSM (pour l'état).
  const lastFsm = (() => {
    for (const run of [...fsmRuns].reverse()) {
      const es = run.entries ?? [];
      if (es.length) return es[es.length - 1];
    }
    return null;
  })();

  const sel = agents.find((a: AgentItem) => a.id === selAgent);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', fontSize: 12, overflow: 'hidden' }}>
      {/* ── Barre du haut : sélection agent ── */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '6px 8px', borderBottom: '1px solid var(--mw-border, #1e293b)', flexWrap: 'wrap' }}>
        <span style={{ color: '#94a3b8' }}>{label(t, 'select_agent', 'Agent')} :</span>
        <CustomSelect
          value={selTeam}
          onChange={(v) => { setSelTeam(v); setSelAgent(null); }}
          options={teams.map((tm) => ({
            value: tm,
            label: tm === 'toutes' ? label(t, 'toutes_equipes', 'Toutes les équipes')
              : tm === 'hors-team' ? label(t, 'hors_team', 'Hors team') : tm,
          }))}
          testid="viewagent-team"
        />
        <CustomSelect
          value={selAgent != null ? String(selAgent) : ''}
          onChange={(v) => setSelAgent(v ? Number(v) : null)}
          options={filtered.map((a: AgentItem) => ({ value: String(a.id), label: a.name }))}
          placeholder="—"
          disabled={filtered.length === 0}
          testid="viewagent-agent"
        />
      </div>

      {!selAgent && (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748b' }}>
          {label(t, 'vide', 'Sélectionne un agent')}
        </div>
      )}

      {selAgent && (
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '260px 1fr 1fr', gap: 6, padding: 6, overflow: 'hidden', minHeight: 0 }}>
          {/* ── Colonne gauche : données agent ── */}
          <div style={{ overflow: 'auto', border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6, padding: 6 }}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>{sel?.name ?? `#${selAgent}`}</div>
            {agent && (
              <div style={{ color: '#94a3b8', marginBottom: 4 }}>
                <div>{label(t, 'status', 'Statut')} : {agent.status ?? '—'}</div>
                <div>{label(t, 'role', 'Rôle')} : {agent.role_type ?? '—'}</div>
                <div>{label(t, 'occupation', 'Occupation')} : {agent.occupation ?? '—'}</div>
                <div>{label(t, 'derniere_activite', 'Dernière activité')} : {agent.last_active_at ?? '—'}</div>
                <div>{label(t, 'requetes', 'Requêtes')} : {met.requests ?? 0} · {label(t, 'tokens', 'Tokens')} : {met.total_tokens ?? 0} · {label(t, 'echecs', 'Échecs')} : {met.errors ?? met.failed_tasks ?? 0}</div>
              </div>
            )}
            {lastFsm && (
              <div style={{ color: '#4ade80', fontSize: 11, margin: '4px 0', fontFamily: 'monospace' }}>
                ▶ {lastFsm.kind} {String(lastFsm.message).slice(0, 60)}
              </div>
            )}

            <div style={{ fontWeight: 700, marginTop: 8, marginBottom: 3 }}>{label(t, 'taches', 'Tâches')}</div>
            {(tasks.data ?? []).length === 0 && (
              <div style={{ color: '#64748b', fontSize: 11 }}>{label(t, 'aucune_tache', 'Aucune tâche')}</div>
            )}
            {(tasks.data ?? []).map((tk: any) => (
              <div key={tk.task_id} style={{ fontSize: 11, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)33' }}>
                <span style={{ color: '#fbbf24' }}>#{tk.task_id}</span>{' '}
                <span style={{ color: '#94a3b8' }}>[{tk.status}]</span>{' '}
                <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{tk.title}</span>
              </div>
            ))}

            <div style={{ fontWeight: 700, marginTop: 8, marginBottom: 3 }}>{label(t, 'variables', 'Variables')}</div>
            {Object.entries(variables).map(([k, v]) => (
              <div key={k} style={{ fontSize: 11, marginBottom: 2 }}>
                <span style={{ color: '#60a5fa' }}>{k}</span>{' '}
                <span style={{ color: '#94a3b8' }}>= {typeof v === 'string' ? (v.length > 80 ? v.slice(0, 80) + '…' : v) : JSON.stringify(v)}</span>
              </div>
            ))}
          </div>

          {/* ── Colonne milieu : conversation LLM ── */}
          <div style={{ overflow: 'auto', border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6, padding: 6 }}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>
              {label(t, 'conversation', 'Conversation LLM')} ({conv.length})
            </div>
            {conv.length === 0 && <div style={{ color: '#64748b' }}>—</div>}
            {conv.map((b: any, i: number) => {
              const open = expanded.has(i);
              const hasContent = !!b.content;
              const tools = b.tools ?? [];
              const sends = b.sends ?? [];
              const err = b.error;
              return (
                <div key={i} style={{ marginBottom: 6, border: '1px solid var(--mw-border, #1e293b)55', borderRadius: 4, padding: 4 }}>
                  {/* Header foldable */}
                  <div style={{ display: 'flex', gap: 4, alignItems: 'center', cursor: 'pointer', flexWrap: 'wrap' }}
                    onClick={() => toggle(i)}>
                    <span style={{ color: open ? '#3b82f6' : '#64748b' }}>{open ? '▼' : '▶'}</span>
                    <span style={{ ...mono, color: b.ok ? '#4ade80' : '#f87171' }}>{b.ok ? 'ok' : 'err'}</span>
                    <span style={{ color: '#94a3b8' }}>r{b.round}</span>
                    <span style={{ color: '#60a5fa' }}>{b.provider}/{b.model}</span>
                    {b.finish && <span style={{ color: '#c084fc' }}>{b.finish}</span>}
                  </div>
                  {!open && hasContent && (
                    <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {String(b.content).slice(0, 120)}{String(b.content).length > 120 ? '…' : ''}
                    </div>
                  )}
                  {open && (
                    <div style={{ marginTop: 4, fontSize: 11 }}>
                      {/* ── ENVOI : ce qui a été envoyé au LLM (repliable) ── */}
                      {sends.length > 0 && (
                        <div style={{ marginBottom: 6 }}>
                          <div style={{ color: '#64748b', fontWeight: 600, marginBottom: 2 }}>
                            {label(t, 'envoye', 'Envoyé')} ({sends.length})
                          </div>
                          <details style={{ marginBottom: 2 }} open>
                            <summary style={{ color: '#818cf8', cursor: 'pointer', fontSize: 11 }}>{label(t, 'details', 'Détails')}</summary>
                            <div style={{ marginLeft: 10 }}>
                              {sends.map((s: any, k: number) => (
                                <div key={k} style={{ marginBottom: 3 }}>
                                  <span style={{
                                    color: s.role === 'user' ? '#4ade80'
                                      : s.role === 'assistant' ? '#60a5fa'
                                      : s.role === 'system' ? '#c084fc'
                                      : s.role === 'tool' ? '#fbbf24' : '#94a3b8',
                                    fontWeight: 600,
                                  }}>[{s.role}]</span>
                                  {s.tool ? (
                                    <span style={{ color: '#fbbf24' }}> → tool {s.text}</span>
                                  ) : (
                                    <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#cbd5e1', fontFamily: 'monospace', marginTop: 1, maxHeight: 140, overflow: 'auto' }}>
                                      {String(s.text).slice(0, 600)}{String(s.text).length > 600 ? '…' : ''}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          </details>
                        </div>
                      )}
                      {hasContent && (
                        <div style={{ marginBottom: 4 }}>
                          <div style={{ color: '#64748b', fontWeight: 600 }}>{label(t, 'content', 'Contenu')}</div>
                          <pre style={{ ...mono, whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--mw-bg2, #0b1220)', padding: 4, borderRadius: 4, color: '#e2e8f0' }}>{b.content}</pre>
                        </div>
                      )}
                      {tools.length > 0 && (
                        <div style={{ marginBottom: 4 }}>
                          <div style={{ color: '#64748b', fontWeight: 600 }}>{label(t, 'tools', 'Outils')} ({tools.length})</div>
                          {tools.map((tl: any, j: number) => (
                            <div key={j} style={{ marginBottom: 2 }}>
                              <span style={{ color: '#fbbf24' }}>{tl.name}</span>
                              <div style={{ marginLeft: 12, fontFamily: 'monospace', color: '#94a3b8', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 120, overflow: 'auto' }}>
                                {String(tl.args).slice(0, 400)}{String(tl.args).length > 400 ? '…' : ''}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      {err && (
                        <div style={{ color: '#f87171', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          {err}
                        </div>
                      )}
                      {b.usage && (
                        <details style={{ marginTop: 2 }}>
                          <summary style={{ color: '#64748b', cursor: 'pointer' }}>{label(t, 'usage', 'Usage')}</summary>
                          <pre style={{ ...mono, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#94a3b8' }}>{b.usage}</pre>
                        </details>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* ── Colonne droite : FSM log / graph ── */}
          <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6 }}>
            <div style={{ display: 'flex', gap: 4, padding: 4, borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
              {(['fsm', 'graph'] as const).map((v) => (
                <button key={v} onClick={() => setView(v)}
                  style={{
                    fontSize: 11, padding: '2px 8px', cursor: 'pointer', borderRadius: 4,
                    background: view === v ? 'var(--mw-accent, #3b82f6)' : 'transparent',
                    color: view === v ? '#fff' : '#94a3b8', border: '1px solid var(--mw-border, #334155)',
                  }}>
                  {v === 'fsm' ? label(t, 'fsm_log', 'Log FSM') : label(t, 'graph', 'Graph')}
                </button>
              ))}
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: 6, fontFamily: 'monospace', fontSize: 11 }}>
              {view === 'fsm' && (
                <>
                  {fsmRuns.length === 0 && <div style={{ color: '#64748b' }}>—</div>}
                  {fsmRuns.map((run: any, ri: number) => (
                    <div key={ri} style={{ marginBottom: 6 }}>
                      <div style={{ color: '#60a5fa', fontWeight: 600, marginBottom: 2 }}>{run.file}</div>
                      {(run.entries ?? []).map((e: any, j: number) => (
                        <div key={j} style={{ marginBottom: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          <span style={{ color: '#64748b' }}>{e.ts}</span>{' '}
                          <span style={{ color: e.level === 'warn' ? '#fbbf24' : e.level === 'error' ? '#f87171' : '#94a3b8' }}>
                            {e.kind}
                          </span>{' '}
                          <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{String(e.message).slice(0, 200)}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </>
              )}
              {view === 'graph' && (
                <>
                  {graphData.length === 0 && <div style={{ color: '#64748b' }}>—</div>}
                  {graphData.map((g: any, i: number) => (
                    <div key={i} style={{ marginBottom: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      <span style={{ color: '#64748b' }}>{g.ts}</span>{' '}
                      <span style={{ color: '#c084fc' }}>{g.kind}</span>{' '}
                      <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{g.label}</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'view-agent',
  labelKey: 'panels.view-agent.titre',
  iconKey: 'panels.view-agent.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['agents'],
  paramsSchema: {
    team_name: { type: 'string', default: 'dev-chat' },
  },
  defaultParams: { team_name: 'dev-chat' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[view-agent] Vue agent v1.0.0\n  données agent · conversation LLM · log FSM/graph\n  routes: agent/list-by-team, agent/get, agent/logs, agent/graph, agent/metrics, workspace/tasks/list',
  component: ViewAgentPanel,
};

export const langFr = LANG_FR;
