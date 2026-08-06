// monitoring/workspace — moniteur issues + tâches d'un workspace (greedy).
// Routes : workspace/issues/list, workspace/tasks/list (poll 10s).
// Param `workspace_id` (défaut "mw-dev-chat").

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  monitoring-workspace:
    titre: "Workspace"
    issues: "Issues"
    taches: "Tâches"
    titre_tache: "Tâche"
    role: "Rôle"
    statut: "Statut"
    priorite: "Prio"
    difficulte: "Difficulté"
    vide: "Aucune tâche"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  monitoring-workspace:
    titre: "Workspace"
    issues: "Issues"
    taches: "Tasks"
    titre_tache: "Task"
    role: "Role"
    statut: "Status"
    priorite: "Prio"
    difficulte: "Difficulty"
    vide: "No tasks"
    erreur: "Error"
`;

const STATUS_COLOR: Record<string, string> = {
  pending: '#f59e0b',
  running: '#3b82f6',
  done: '#4ade80',
  failed: '#f87171',
};

function WorkspacePanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const workspaceId = params.workspace_id ?? 'mw-dev-chat';
  const team = params.team ?? '';
  const baseTasksBody = { workspace_id: workspaceId, all: true };

  const issues = usePoll<any>(ctx.api.post, 'workspace/issues/list', { workspace_id: workspaceId }, 15000,
    (res) => unwrapResult(res).issues ?? []);
  const tasks = usePoll<any>(ctx.api.post, 'workspace/tasks/list', baseTasksBody, 10000,
    (res) => unwrapResult(res).tasks ?? []);

  // Filtre client : seules les tâches de la team demandée (préfixe team:XXX/).
  const tasksData = team
    ? (tasks.data ?? []).filter((t: any) => String(t.assigned_to ?? '').startsWith(`team:${team}/`))
    : (tasks.data ?? []);

  const statusBadge = (s: string) => (
    <span style={{
      fontSize: 10, padding: '1px 6px', borderRadius: 8,
      background: (STATUS_COLOR[s] ?? '#64748b') + '22',
      color: STATUS_COLOR[s] ?? '#94a3b8',
    }}>{s}</span>
  );

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>
        {workspaceId}{team ? ` · team ${team}` : ''}
      </div>

      <div style={{ fontWeight: 600, marginBottom: 4 }}>{ctx.t?.('panels.monitoring-workspace.issues') ?? 'Issues'} ({issues.data?.length ?? 0})</div>
      {issues.error && <div style={{ color: '#f87171' }}>{issues.error}</div>}
      {(issues.data ?? []).slice(0, 5).map((it: any) => (
        <div key={it.issue_id} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '2px 0' }}>
          <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{it.title}</span>
          {statusBadge(it.status ?? 'open')}
        </div>
      ))}

      <div style={{ fontWeight: 600, margin: '8px 0 4px' }}>{ctx.t?.('panels.monitoring-workspace.taches') ?? 'Tâches'} ({tasksData.length}{team ? ` / ${tasks.data?.length ?? 0}` : ''})</div>
      {tasks.error && <div style={{ color: '#f87171' }}>{tasks.error}</div>}
      {tasksData.length === 0 && !tasks.error && (
        <div style={{ color: '#64748b' }}>{ctx.t?.('panels.monitoring-workspace.vide') ?? 'Aucune tâche'}</div>
      )}
      {tasksData.map((t: any) => (
        <div key={t.task_id} style={{ border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6, padding: '4px 6px', marginBottom: 4 }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--mw-fg, #e2e8f0)' }}>{t.title}</span>
            {statusBadge(t.status)}
          </div>
          <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2 }}>
            #{t.task_id} · {ctx.t?.('panels.monitoring-workspace.role') ?? 'Rôle'} : {t.role_required || '—'} ·{' '}
            {ctx.t?.('panels.monitoring-workspace.difficulte') ?? 'Diff'} : {t.difficulty || '—'} ·{' '}
            {ctx.t?.('panels.monitoring-workspace.priorite') ?? 'Prio'} : {t.priority ?? 0}
            {t.assigned_to ? ` · assigné : ${t.assigned_to}` : ''}
          </div>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'monitoring-workspace',
  labelKey: 'panels.monitoring-workspace.titre',
  iconKey: 'panels.monitoring-workspace.titre',
  version: '1.1.0',
  essential: false,
  bundles: ['monitoring', 'projet'],
  paramsSchema: {
    workspace_id: { type: 'string', default: 'mw-dev-chat' },
    team: { type: 'string', default: '' },
  },
  defaultParams: { workspace_id: 'mw-dev-chat', team: '' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[monitoring-workspace] Workspace v1.1.0\n  tâches/issues du workspace (défaut mw-dev-chat), filtre team',
  component: WorkspacePanel,
};

export const langFr = LANG_FR;
