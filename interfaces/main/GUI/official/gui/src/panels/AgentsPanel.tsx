import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner, sigBtn } from '../components/ui.tsx';

function AgentCard({ a, app }: { a: any; app: AppApi }) {
  const shortName = a.name.includes('/') ? a.name.split('/').pop() : a.name;
  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.8rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: '0.85rem', fontWeight: '600', color: '#e2e8f0' }}>
            {shortName}
            {a.running && <span style={{ color: '#6ee7b7', fontSize: '0.7rem', marginLeft: '0.3rem' }}>● actif</span>}
            {app.agentMgr.zombies.includes(a.agent_id) && <span style={{ color: '#fca5a5', fontSize: '0.7rem', marginLeft: '0.3rem' }}>🧟 zombie</span>}
          </div>
          <div style={{ fontSize: '0.66rem', color: '#64748b' }}>
            {a.role_type} · {a.occupation} · <span style={{ color: a.status === 'RUNNING' ? '#6ee7b7' : '#94a3b8' }}>{a.status}</span>
            {a.running && a.heartbeat ? ` · ❤ ${Math.round(a.heartbeat)} ms` : ''}
            {a.running && a.current_step ? ` · 🪜 ${a.current_step}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
          <button onClick={() => app.sendAgentSignal(a.agent_id, 'pause')} style={sigBtn('#475569')}>⏸ Pause</button>
          <button onClick={() => app.sendAgentSignal(a.agent_id, 'resume')} style={sigBtn('#0e7490')}>▶ Reprendre</button>
          <button onClick={() => app.sendAgentSignal(a.agent_id, 'configure', { variables: { note: 'via-gui' } })} style={sigBtn('#7c3aed')}>⚙ Config</button>
          <button onClick={() => app.sendAgentSignal(a.agent_id, 'kill')} style={sigBtn('#7f1d1d')}>✕ Kill</button>
          <button onClick={() => app.watchAgentStream(a.agent_id)} style={sigBtn('#059669')}>📡 Stream</button>
        </div>
        <div style={{ display: 'flex', gap: '0.3rem', marginTop: '0.3rem' }}>
          <button onClick={() => app.handleAgentRestart(a.name)} style={{ ...sigBtn('#1d4ed8'), fontSize: '0.6rem', padding: '0.15rem 0.35rem' }}>⟳ Restart</button>
          <button onClick={() => app.handleAgentStop(a.name)} style={{ ...sigBtn('#7f1d1d'), fontSize: '0.6rem', padding: '0.15rem 0.35rem' }}>■ Stop</button>
        </div>
      </div>
    </div>
  );
}

function AgentTeamGroup({ teamName, data, app }: { teamName: string; data: { agents: any[]; team_info?: any }; app: AppApi }) {
  const info = data.team_info || {};
  const statusColor = info.status === 'running' ? '#6ee7b7' : info.status === 'stopped' ? '#f87171' : '#94a3b8';
  return (
    <div style={{ marginBottom: '0.6rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.4rem', padding: '0.3rem 0.5rem', backgroundColor: '#0f172a', borderRadius: '0.3rem', border: '1px solid #334155' }}>
        <span style={{ fontSize: '0.8rem', fontWeight: '700', color: '#93c5fd' }}>▸ {teamName}</span>
        {info.status ? <span style={{ fontSize: '0.65rem', color: statusColor }}>· {info.status}</span> : null}
        {info.topology ? <span style={{ fontSize: '0.6rem', color: '#64748b' }}>({info.topology})</span> : null}
        <span style={{ fontSize: '0.6rem', color: '#64748b', marginLeft: 'auto' }}>{data.agents.length} membre{data.agents.length > 1 ? 's' : ''}</span>
      </div>
      {data.agents.map((a: any) => <AgentCard key={a.agent_id} a={a} app={app} />)}
    </div>
  );
}

export function AgentsPanel({ app }: { app: AppApi }) {
  const teams = app.agentTeams || {};
  const standalone = app.agentStandalone || [];
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
        <h3 style={{ fontSize: '1rem', fontWeight: '600' }}>🤖 Agents (groupés par équipe)</h3>
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
          {Object.keys(teams).length === 0 && standalone.length === 0 && !app.agentLoading && (
            <div style={{ color: '#94a3b8', fontSize: '0.8rem', padding: '1rem', backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155' }}>
              Aucun agent. Créez-en un via <code>agent/create</code>.
            </div>
          )}
          {Object.entries(teams).map(([tn, td]) => (
            <AgentTeamGroup key={tn} teamName={tn} data={td as any} app={app} />
          ))}
          {standalone.length > 0 && (
            <div>
              <div style={{ fontSize: '0.75rem', fontWeight: '600', color: '#94a3b8', padding: '0.3rem 0.5rem', marginBottom: '0.3rem' }}>Agents sans équipe</div>
              {standalone.map((a: any) => <AgentCard key={a.agent_id} a={a} app={app} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
