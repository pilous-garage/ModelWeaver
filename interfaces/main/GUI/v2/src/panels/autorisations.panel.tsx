// autorisations — demandes d'autorisation en attente (membres → leader → humain).
// Routes : auth/list (poll), auth/decide (allow/deny/escalate/ask_reason).
// Affiche les demandes avec agent demandeur, action, cible, raison, scope,
// et permet de décider avec une portée (once/run/day/forever).

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';
import { CustomSelect } from '../components/CustomSelect.tsx';

const LANG_FR = `
panels:
  autorisations:
    titre: "Autorisations"
    en_attente: "Demandes en attente"
    leader: "Leader"
    humain: "Humain"
    agent: "Agent"
    action: "Action"
    cible: "Cible"
    raison: "Raison"
    scope: "Portée"
    autoriser: "Autoriser"
    refuser: "Refuser"
    transmettre: "→ Humain"
    demander: "Pourquoi ?"
    vide: "Aucune demande en attente"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  autorisations:
    titre: "Authorizations"
    en_attente: "Pending requests"
    leader: "Leader"
    humain: "Human"
    agent: "Agent"
    action: "Action"
    cible: "Target"
    raison: "Reason"
    scope: "Scope"
    autoriser: "Allow"
    refuser: "Deny"
    transmettre: "→ Human"
    demander: "Why?"
    vide: "No pending requests"
    erreur: "Error"
`;

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

function AutorisationsPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const [level, setLevel] = useState<'leader' | 'human'>('leader');
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'auth/list', { level, status: 'pending' }, 4000,
    (res) => unwrapResult(res), true,
  );
  const requests: any[] = data?.requests ?? [];

  const decide = async (requestId: string, decision: string) => {
    const scope = scopes[requestId] || 'once';
    try {
      await ctx.api.post('auth/decide', {
        request_id: requestId, decision, scope,
        approver_id: level === 'human' ? 'human-gui' : 'leader-gui',
      });
      reload();
    } catch (e: any) { console.error('auth/decide', e); }
  };

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {/* En-tête : bascule leader / humain */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }}>
        <span style={{ fontWeight: 700 }}>
          {ctx.t?.('panels.autorisations.en_attente') ?? 'Demandes en attente'} ({requests.length})
        </span>
        <button
          onClick={() => setLevel('leader')}
          style={{
            padding: '2px 10px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
            border: '1px solid var(--mw-border, #334155)',
            background: level === 'leader' ? 'var(--mw-accent, #3b82f6)' : 'transparent',
            color: level === 'leader' ? '#fff' : '#94a3b8',
          }}>
          {ctx.t?.('panels.autorisations.leader') ?? 'Leader'}
        </button>
        <button
          onClick={() => setLevel('human')}
          style={{
            padding: '2px 10px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
            border: '1px solid var(--mw-border, #334155)',
            background: level === 'human' ? 'var(--mw-accent, #3b82f6)' : 'transparent',
            color: level === 'human' ? '#fff' : '#94a3b8',
          }}>
          {ctx.t?.('panels.autorisations.humain') ?? 'Humain'}
        </button>
      </div>

      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{error}</div>}

      {requests.length === 0 && (
        <div style={{ color: '#64748b', textAlign: 'center', padding: 20 }}>
          {ctx.t?.('panels.autorisations.vide') ?? 'Aucune demande en attente'}
        </div>
      )}

      {requests.map((a) => (
        <div key={a.request_id} style={{
          marginBottom: 8, padding: 8, borderRadius: 6,
          border: '1px solid var(--mw-border, #334155)',
          background: 'var(--mw-bg-dim, rgba(51,65,85,.15))',
        }}>
          {/* Ligne 1 : agent + action + cible */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 700 }}>{a.agent_id}</span>
            <span style={{ color: '#38bdf8' }}>{ACTION_LABEL[a.action] ?? a.action}</span>
            <code style={{ color: '#e2e8f0', background: 'rgba(0,0,0,.3)', padding: '1px 5px', borderRadius: 4 }}>
              {targetLabel(a)}
            </code>
            {a.scope && <span style={{ color: '#f59e0b', fontSize: 10 }}>demande scope: {a.scope}</span>}
          </div>
          {/* Raison */}
          {a.reason && <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 3 }}>💬 {a.reason}</div>}

          {/* Contrôles : scope + actions */}
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
            <CustomSelect
              value={scopes[a.request_id] || 'once'}
              onChange={(v) => setScopes((s) => ({ ...s, [a.request_id]: v }))}
              options={SCOPES.map((s) => ({ value: s, label: s }))}
              testid="autoris-scope"
              maxWidth={110}
            />
            <button className="mw-btn" style={{ fontSize: 11, color: '#4ade80', border: '1px solid #4ade80', background: 'transparent', cursor: 'pointer', padding: '2px 10px', borderRadius: 4 }}
              onClick={() => decide(a.request_id, 'allow')}>
              {ctx.t?.('panels.autorisations.autoriser') ?? 'Autoriser'}
            </button>
            <button className="mw-btn" style={{ fontSize: 11, color: '#f87171', border: '1px solid #f87171', background: 'transparent', cursor: 'pointer', padding: '2px 10px', borderRadius: 4 }}
              onClick={() => decide(a.request_id, 'deny')}>
              {ctx.t?.('panels.autorisations.refuser') ?? 'Refuser'}
            </button>
            {level === 'leader' && (
              <button className="mw-btn" style={{ fontSize: 11, color: '#fbbf24', border: '1px solid #fbbf24', background: 'transparent', cursor: 'pointer', padding: '2px 10px', borderRadius: 4 }}
                onClick={() => decide(a.request_id, 'escalate')}>
                {ctx.t?.('panels.autorisations.transmettre') ?? '→ Humain'}
              </button>
            )}
            <button className="mw-btn" style={{ fontSize: 11, color: '#94a3b8', border: '1px solid #334155', background: 'transparent', cursor: 'pointer', padding: '2px 10px', borderRadius: 4 }}
              onClick={() => decide(a.request_id, 'ask_reason')}>
              {ctx.t?.('panels.autorisations.demander') ?? 'Pourquoi ?'}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'autorisations',
  labelKey: 'panels.autorisations.titre',
  iconKey: 'panels.autorisations.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['communication', 'projet'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[autorisations] Demandes d\'autorisation v1.0.0\n  routes: auth/list, auth/decide',
  component: AutorisationsPanel,
};

export const langFr = LANG_FR;
