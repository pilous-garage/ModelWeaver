import React, { useState, useCallback } from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner, sigBtn } from '../components/ui.tsx';

function DraggableRole({ name, desc }: { name: string; desc: string }) {
  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('text/plain', JSON.stringify({ role: name, agentName: name }));
    e.dataTransfer.effectAllowed = 'copy';
  };
  return (
    <div draggable onDragStart={handleDragStart}
      style={{
        padding: '0.3rem 0.5rem', marginBottom: '0.2rem',
        backgroundColor: '#1e293b', borderRadius: '0.3rem',
        border: '1px solid #334155', cursor: 'grab',
        fontSize: '0.68rem', color: '#e2e8f0',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
      <span style={{ fontWeight: '600' }}>{name}</span>
      <span style={{ fontSize: '0.55rem', color: '#64748b', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{desc}</span>
    </div>
  );
}

function DropSlot({ label, onDrop, children, compact }: { label: string; onDrop: (role: string, agentName: string) => void; children?: React.ReactNode; compact?: boolean }) {
  const [over, setOver] = useState(false);
  const handleDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setOver(true); }, []);
  const handleDragLeave = useCallback(() => setOver(false), []);
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    try {
      const data = JSON.parse(e.dataTransfer.getData('text/plain'));
      onDrop(data.role, data.agentName);
    } catch { /* ignore */ }
  }, [onDrop]);

  return (
    <div onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}
      style={{
        minHeight: compact ? '1.8rem' : '2.5rem',
        border: `2px dashed ${over ? '#60a5fa' : '#334155'}`,
        borderRadius: '0.4rem',
        padding: '0.3rem',
        backgroundColor: over ? 'rgba(96,165,250,0.08)' : 'transparent',
        transition: 'all 0.15s',
      }}>
      {children ? children : (
        <div style={{ fontSize: '0.6rem', color: '#64748b', textAlign: 'center', padding: '0.3rem' }}>
          {over ? '⟐ Déposer ici' : `⬇ ${label}`}
        </div>
      )}
    </div>
  );
}

function MemberCard({ agent, isLeader, app, teamName }: { agent: any; isLeader?: boolean; app: AppApi; teamName?: string }) {
  const [expanded, setExpanded] = useState(false);
  const borderColor = isLeader ? '#2563eb' : '#334155';
  const bgColor = isLeader ? '#0f2740' : '#1e293b';
  return (
    <div style={{
      backgroundColor: bgColor, borderRadius: '0.5rem', border: `1px solid ${borderColor}`,
      padding: '0.5rem', marginBottom: '0.3rem',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', flex: 1, minWidth: 0 }}>
          {isLeader && <span style={{ fontSize: '0.55rem', backgroundColor: '#1d4ed8', color: '#e2e8f0', padding: '0.1rem 0.35rem', borderRadius: '0.2rem', fontWeight: '600', flexShrink: 0 }}>LEADER</span>}
          <span style={{ fontSize: '0.75rem', fontWeight: '600', color: '#e2e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent.agent_name}</span>
          <span style={{
            fontSize: '0.55rem', padding: '0.05rem 0.3rem', borderRadius: '0.2rem', flexShrink: 0,
            color: agent.status === 'RUNNING' || agent.status === 'IDLE' ? '#6ee7b7' : agent.status === 'STOPPED' ? '#f87171' : '#94a3b8',
            border: '1px solid ' + (agent.status === 'RUNNING' || agent.status === 'IDLE' ? '#064e3b' : agent.status === 'STOPPED' ? '#7f1d1d' : '#334155'),
          }}>{agent.status || '?'}</span>
        </div>
        {!isLeader && (
          <button onClick={() => setExpanded(!expanded)} style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: '0.6rem', flexShrink: 0 }}>
            {expanded ? '▲' : '▼'}
          </button>
        )}
      </div>
      {expanded && !isLeader && (
        <div style={{ marginTop: '0.3rem', paddingTop: '0.3rem', borderTop: '1px solid #334155', fontSize: '0.6rem', color: '#94a3b8' }}>
          <div>ID: {agent.agent_id}</div>
          <div>Dernière activité: {agent.last_active_at || 'jamais'}</div>
        </div>
      )}
    </div>
  );
}

function TeamCard({ team, app }: { team: any; app: AppApi }) {
  const [expanded, setExpanded] = useState(true);
  const statusColor = team.status === 'running' ? '#6ee7b7' : team.status === 'stopped' ? '#f87171' : '#94a3b8';

  const handleDropLeader = async (role: string, agentName: string) => {
    await app.addAgentToTeam(team.name.replace('team:', ''), agentName, role, 'leader');
  };
  const handleDropMember = async (role: string, agentName: string) => {
    await app.addAgentToTeam(team.name.replace('team:', ''), agentName, role, 'member');
  };

  return (
    <div style={{
      backgroundColor: '#0f172a', borderRadius: '0.5rem', border: '1px solid #334155',
      padding: '0.6rem', marginBottom: '0.6rem',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
        onClick={() => setExpanded(!expanded)}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <span style={{ fontSize: '0.85rem', fontWeight: '700', color: '#93c5fd' }}>▸ {team.name}</span>
          <span style={{ fontSize: '0.6rem', color: statusColor }}>· {team.status}</span>
          <span style={{ fontSize: '0.55rem', color: '#64748b' }}>({team.topology})</span>
          <span style={{ fontSize: '0.55rem', color: '#64748b' }}>· {team.member_count} membre{team.member_count > 1 ? 's' : ''}</span>
        </div>
        <span style={{ color: '#64748b', fontSize: '0.65rem' }}>{expanded ? '▲' : '▼'}</span>
      </div>
      {expanded && (
        <div style={{ marginTop: '0.4rem' }}>
          {team.workspace_id && (
            <div style={{ fontSize: '0.55rem', color: '#64748b', marginBottom: '0.3rem' }}>Workspace: {team.workspace_id}</div>
          )}
          {team.team_leader ? (
            <div style={{ marginBottom: '0.4rem' }}>
              <div style={{ fontSize: '0.6rem', color: '#60a5fa', marginBottom: '0.2rem', fontWeight: '600' }}>Team Leader</div>
              <MemberCard agent={team.team_leader} isLeader app={app} teamName={team.name} />
            </div>
          ) : (
            <div style={{ marginBottom: '0.4rem' }}>
              <div style={{ fontSize: '0.6rem', color: '#64748b', marginBottom: '0.2rem' }}>Team Leader <span style={{ color: '#f87171' }}>(vide — glisser un rôle)</span></div>
              <DropSlot label="Déposer un rôle ici pour définir le leader" onDrop={handleDropLeader} />
            </div>
          )}
          {team.members && team.members.length > 0 && (
            <div style={{ marginBottom: '0.3rem' }}>
              <div style={{ fontSize: '0.6rem', color: '#94a3b8', marginBottom: '0.2rem', fontWeight: '600' }}>Membres</div>
              {team.members.map((m: any) => <MemberCard key={m.agent_id} agent={m} app={app} teamName={team.name} />)}
            </div>
          )}
          <DropSlot label="+ Ajouter un membre (glisser un rôle)" onDrop={handleDropMember} compact />
        </div>
      )}
    </div>
  );
}

export function TeamCompositionPanel({ app }: { app: AppApi }) {
  const teams = app.teamList || [];
  const cat = app.agentCatalogue;
  const roles = cat?.roles ? Object.entries(cat.roles as Record<string, any>) : [];

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.4rem', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
        <h3 style={{ fontSize: '0.9rem', fontWeight: '600' }}>📋 Composition des équipes</h3>
        <div style={{ display: 'flex', gap: '0.3rem' }}>
          <button onClick={app.fetchCapabilities} disabled={app.catLoading}
            style={{ padding: '0.2rem 0.5rem', fontSize: '0.62rem', backgroundColor: app.catLoading ? '#1e293b' : '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.3rem', cursor: app.catLoading ? 'default' : 'pointer' }}>
            {app.catLoading ? <Spinner size={10} /> : '↻ Catalogue'}
          </button>
          <button onClick={app.fetchTeams} disabled={app.teamsLoading}
            style={{ padding: '0.2rem 0.5rem', fontSize: '0.62rem', backgroundColor: app.teamsLoading ? '#1e293b' : '#2563eb', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: app.teamsLoading ? 'default' : 'pointer' }}>
            {app.teamsLoading ? <Spinner size={10} /> : '↻ Équipes'}
          </button>
        </div>
      </div>
      <div style={{ flex: 1, display: 'flex', gap: '0.4rem', overflow: 'hidden', minHeight: 0 }}>
        {roles.length > 0 && (
          <div style={{ width: '140px', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '0.2rem', overflow: 'hidden' }}>
            <div style={{ fontSize: '0.6rem', fontWeight: '600', color: '#60a5fa', padding: '0.2rem 0', flexShrink: 0 }}>Catalogue agents</div>
            <div style={{ flex: 1, overflowY: 'auto', paddingRight: '0.2rem' }}>
              <div style={{ fontSize: '0.55rem', color: '#64748b', marginBottom: '0.2rem' }}>Glisser vers une équipe ⬇</div>
              {roles.map(([name, info]: [string, any]) => (
                <DraggableRole key={name} name={name} desc={info.description || ''} />
              ))}
            </div>
          </div>
        )}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {teams.length === 0 && !app.teamsLoading && (
            <div style={{ color: '#94a3b8', fontSize: '0.75rem', padding: '0.8rem', backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155' }}>
              Aucune équipe. Créez-en une via un manifest <code>.team.yaml</code>.
            </div>
          )}
          {teams.map((t: any) => <TeamCard key={t.name} team={t} app={app} />)}
        </div>
      </div>
    </div>
  );
}
