// autorisations — demandes d'autorisation en attente (membres → leader → humain).
// Panel EXTERNE (essential: false) : compilé par panel-creator → servie à chaud
// par le daemon, chargée par la GUI via import() dynamique (pas de rebuild Tauri).
// Routes : auth/list (poll), auth/decide.

import React, { useEffect, useRef, useState } from 'react';
import type { PanelDef } from '../../types.ts';

const SCOPES = ['once', 'run', 'day', 'forever'];

const ACTION_LABEL: Record<string, string> = {
  path_read: '📖 lecture',
  path_write: '✏️ écriture',
  command: '⚙ commande',
};

function targetLabel(a: any): string {
  const t = a?.target ?? {};
  if (a.action === 'command') return t.command ?? '?';
  return t.path ?? JSON.stringify(t);
}

function AutorisationsView({ app }: { app: any }) {
  const [level, setLevel] = useState<'leader' | 'human'>('leader');
  const [requests, setRequests] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const alive = useRef(true);

  const load = async () => {
    try {
      const res = await app.post('auth/list', { level, status: 'pending' });
      const r = res?.result ?? res ?? {};
      if (alive.current) {
        setRequests(Array.isArray(r.requests) ? r.requests : []);
        setError(null);
      }
    } catch (e: any) {
      if (alive.current) setError(String(e?.message ?? e));
    }
  };

  useEffect(() => {
    alive.current = true;
    load();
    const id = setInterval(load, 4000);
    return () => { alive.current = false; clearInterval(id); };
  }, [level]);

  const decide = async (requestId: string, decision: string) => {
    try {
      await app.post('auth/decide', {
        request_id: requestId, decision,
        scope: scopes[requestId] || 'once',
        approver_id: level === 'human' ? 'human-gui' : 'leader-gui',
      });
      load();
    } catch (e: any) { console.error('auth/decide', e); }
  };

  const btn = (label: string, color: string, onClick: () => void) => (
    <button
      onClick={onClick}
      style={{
        fontSize: 11, padding: '2px 10px', borderRadius: 4, cursor: 'pointer',
        color, border: `1px solid ${color}`, background: 'transparent',
      }}>
      {label}
    </button>
  );

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }}>
        <span style={{ fontWeight: 700 }}>Demandes en attente ({requests.length})</span>
        {(['leader', 'human'] as const).map((l) => (
          <button key={l} onClick={() => setLevel(l)}
            style={{
              padding: '2px 10px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
              border: '1px solid var(--mw-border, #334155)',
              background: level === l ? 'var(--mw-accent, #3b82f6)' : 'transparent',
              color: level === l ? '#fff' : '#94a3b8',
            }}>
            {l === 'leader' ? 'Leader' : 'Humain'}
          </button>
        ))}
      </div>

      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{error}</div>}
      {requests.length === 0 && !error && (
        <div style={{ color: '#64748b', textAlign: 'center', padding: 20 }}>Aucune demande en attente</div>
      )}

      {requests.map((a: any) => (
        <div key={a.request_id} style={{
          marginBottom: 8, padding: 8, borderRadius: 6,
          border: '1px solid var(--mw-border, #334155)',
          background: 'var(--mw-bg-dim, rgba(51,65,85,.15))',
        }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 700 }}>{a.agent_id}</span>
            <span style={{ color: '#38bdf8' }}>{ACTION_LABEL[a.action] ?? a.action}</span>
            <code style={{ color: '#e2e8f0', background: 'rgba(0,0,0,.3)', padding: '1px 5px', borderRadius: 4 }}>
              {targetLabel(a)}
            </code>
            {a.scope && <span style={{ color: '#f59e0b', fontSize: 10 }}>scope: {a.scope}</span>}
          </div>
          {a.reason && <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 3 }}>💬 {a.reason}</div>}

          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
            <select
              value={scopes[a.request_id] || 'once'}
              onChange={(e) => setScopes((s) => ({ ...s, [a.request_id]: e.target.value }))}
              style={{ fontSize: 11, padding: '2px 4px', background: 'var(--mw-bg, #0f172a)', color: '#e2e8f0', border: '1px solid var(--mw-border, #334155)', borderRadius: 4 }}>
              {SCOPES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            {btn('Autoriser', '#4ade80', () => decide(a.request_id, 'allow'))}
            {btn('Refuser', '#f87171', () => decide(a.request_id, 'deny'))}
            {level === 'leader' && btn('→ Humain', '#fbbf24', () => decide(a.request_id, 'escalate'))}
            {btn('Pourquoi ?', '#94a3b8', () => decide(a.request_id, 'ask_reason'))}
          </div>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: "autorisations",
  label: "Autorisations",
  icon: "shield",
  essential: false,
  version: "1.0.0",
  description: "Demandes d'autorisation en attente (leader → humain)",
  daemonRoutes: [
    { route: "auth/list", methods: ["POST"], desc: "Liste des demandes en attente" },
    { route: "auth/decide", methods: ["POST"], desc: "Décider (allow/deny/escalate)" },
  ],
  declaration: () => [
    "[autorisations] Autorisations v1.0.0",
    "  Routes: auth/list, auth/decide",
  ].join("\n"),
  component: ({ ctx }) => React.createElement(AutorisationsView, { app: ctx.api }),
};
