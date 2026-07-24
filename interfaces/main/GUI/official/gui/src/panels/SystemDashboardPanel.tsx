import React from 'react';
import type { AppApi } from '../useApp.ts';
import { MenuBar } from '../components/MenuBar.tsx';
import { AgentsPanel } from './AgentsPanel.tsx';
import { LocalModelsPanel } from './LocalModelsPanel.tsx';
import { SystemStatePanel } from './SystemStatePanel.tsx';
import { ResourcesPanel } from './ResourcesPanel.tsx';
import { KeysPanel } from './KeysPanel.tsx';
import { DebugPanel } from './DebugPanel.tsx';
import AgentLauncherPanel from './AgentLauncherPanel.tsx';
import { ServicesMonitorPanel } from './ServicesMonitorPanel.tsx';
import AgentLauncherPanel from './AgentLauncherPanel.tsx';

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '0.5rem',
      border: '1px solid #334155',
      padding: '0.75rem',
      overflow: 'auto',
    }}>
      <h3 style={{ fontSize: '0.82rem', fontWeight: '600', marginBottom: '0.5rem', color: '#94a3b8' }}>{title}</h3>
      {children}
    </div>
  );
}

export function SystemDashboardPanel({ app }: { app: AppApi }) {
  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: '#0f172a',
      color: '#e2e8f0',
      fontFamily: 'sans-serif',
      overflow: 'hidden',
    }}>
      <style>{`@keyframes mw-spin { to { transform: rotate(360deg); } }`}</style>

      <MenuBar app={app} />

      <div style={{
        padding: '0.5rem 1rem',
        borderBottom: '1px solid #334155',
        display: 'flex',
        alignItems: 'center',
        gap: '0.6rem',
        fontSize: '0.78rem',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 'bold', fontSize: '0.9rem' }}>ModelWeaver</span>
        <span style={{ color: '#64748b' }}>
          {app.appVersion ? `v${app.appVersion}` : 'v…'}
        </span>
        <span style={{ color: '#475569', marginLeft: '0.5rem' }}>— Dashboard</span>
        <span style={{ color: '#6ee7b7', marginLeft: '0.5rem' }}>● {app.agentMgr.active_agents} actifs</span>
        <div style={{ flex: 1 }} />
        <button onClick={app.toggleFullscreen}
          style={{ padding: '0.25rem 0.5rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.7rem' }}>
          {app.isFullscreen ? '🗗' : '⛶'}
        </button>
      </div>

      <div style={{ flex: 1, padding: '0.3rem', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
        <div style={{ display: 'flex', gap: '0.3rem', flex: 1, minHeight: 0 }}>
          <div style={{ flex: 1 }}>
            <SectionCard title=" État système">
              <SystemStatePanel app={app} />
            </SectionCard>
          </div>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Ressources">
              <ResourcesPanel app={app} />
            </SectionCard>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.3rem', flex: 1, minHeight: 0 }}>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Agents">
              <AgentsPanel app={app} />
            </SectionCard>
          </div>
          <div style={{ flex: 1 }}>
            <SectionCard title=" LLM locaux">
              <LocalModelsPanel app={app} />
            </SectionCard>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.3rem', flex: 1, minHeight: 0 }}>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Clés API">
              <KeysPanel app={app} />
            </SectionCard>
          </div>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Lanceur d'agents solo">
              <AgentLauncherPanel app={app} />
            </SectionCard>
          </div>
        </div>

        <div style={{ flex: 1, minHeight: 0 }}>
          <SectionCard title=" Moniteur de services">
            <ServicesMonitorPanel app={app} />
          </SectionCard>
        </div>

        <div style={{ flex: 1, minHeight: 0 }}>
          <SectionCard title=" Débogage">
            <DebugPanel app={app} />
          </SectionCard>
        </div>
      </div>
    </div>
  );
}