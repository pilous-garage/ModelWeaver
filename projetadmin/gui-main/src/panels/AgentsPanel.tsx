import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner, sigBtn } from '../components/ui.tsx';

export function AgentsPanel({ app }: { app: AppApi }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
        <h3 style={{ fontSize: '1rem', fontWeight: '600' }}>🤖 Agents (signaux & streaming)</h3>
        <button onClick={app.fetchAgents} disabled={app.agentLoading}
          style={{ padding: '0.3rem 0.7rem', fontSize: '0.72rem', backgroundColor: app.agentLoading ? '#1e293b' : '#2563eb', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: app.agentLoading ? 'default' : 'pointer' }}>
          {app.agentLoading ? <Spinner size={12} color="#64748b" /> : '↻ Actualiser'}
        </button>
      </div>
      <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', fontSize: '0.7rem' }}>
        <span style={{ backgroundColor: '#052e16', color: '#6ee7b7', padding: '0.2rem 0.5rem', borderRadius: '0.3rem', border: '1px solid #064e3b' }}>● Actifs : {app.agentMgr.active_agents}</span>
        <span style={{ backgroundColor: app.agentMgr.zombies.length ? '#2a0a0a' : '#1e293b', color: app.agentMgr.zombies.length ? '#fca5a5' : '#94a3b8', padding: '0.2rem 0.5rem', borderRadius: '0.3rem', border: '1px solid ' + (app.agentMgr.zombies.length ? '#7f1d1d' : '#334155') }}>🧟 Zombies : {app.agentMgr.zombies.length}{app.agentMgr.zombies.length ? ' (' + app.agentMgr.zombies.join(', ') + ')' : ''}</span>
      </div>
      {app.agentStreamAgent != null ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.6rem', overflow: 'hidden' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.8rem', color: '#93c5fd' }}>Stream temps réel · agent #{app.agentStreamAgent}</span>
            <button onClick={app.stopAgentStream} style={{ padding: '0.25rem 0.6rem', fontSize: '0.7rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: 'none', borderRadius: '0.3rem', cursor: 'pointer' }}>■ Arrêter</button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.8rem', fontSize: '0.78rem', whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>
            {app.agentStreamText || '… (en attente de chunks)'}
          </div>
          <div style={{ fontSize: '0.7rem', color: '#64748b' }}>Signaux : {app.agentSignals.length ? app.agentSignals.map((s: any) => `${s.type}:${s.status}`).join(' · ') : 'aucun'}</div>
        </div>
      ) : (
        <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {app.agentList.length === 0 && !app.agentLoading && (
            <div style={{ color: '#94a3b8', fontSize: '0.8rem', padding: '1rem', backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155' }}>
              Aucun agent. Créez-en un via <code>agent/create</code>.
            </div>
          )}
          {app.agentList.map((a: any) => (
            <div key={a.agent_id} style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.8rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                 <div>
                   <div style={{ fontSize: '0.85rem', fontWeight: '600', color: '#e2e8f0' }}>{a.name} {a.running && <span style={{ color: '#6ee7b7', fontSize: '0.7rem' }}>● actif</span>}{app.agentMgr.zombies.includes(a.agent_id) && <span style={{ color: '#fca5a5', fontSize: '0.7rem' }}>🧟 zombie</span>}</div>
                   <div style={{ fontSize: '0.66rem', color: '#64748b' }}>{a.role_type} · {a.occupation} · <span style={{ color: a.status === 'RUNNING' ? '#6ee7b7' : '#94a3b8' }}>{a.status}</span>{a.running && a.heartbeat ? ` · ❤ ${Math.round(a.heartbeat)} ms` : ''}{a.running && a.current_step ? ` · 🪜 ${a.current_step}` : ''}</div>
                 </div>
                <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                  <button onClick={() => app.sendAgentSignal(a.agent_id, 'pause')} style={sigBtn('#475569')}>⏸ Pause</button>
                  <button onClick={() => app.sendAgentSignal(a.agent_id, 'resume')} style={sigBtn('#0e7490')}>▶ Reprendre</button>
                  <button onClick={() => app.sendAgentSignal(a.agent_id, 'configure', { variables: { note: 'via-gui' } })} style={sigBtn('#7c3aed')}>⚙ Config</button>
                  <button onClick={() => app.sendAgentSignal(a.agent_id, 'kill')} style={sigBtn('#7f1d1d')}>✕ Kill</button>
                  <button onClick={() => app.watchAgentStream(a.agent_id)} style={sigBtn('#059669')}>📡 Stream</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
