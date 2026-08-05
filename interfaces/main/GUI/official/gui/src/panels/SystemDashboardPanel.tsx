import React from 'react';
import type { AppApi } from '../useApp.ts';
import { AgentsPanel } from './AgentsPanel.tsx';
import { LocalModelsPanel } from './LocalModelsPanel.tsx';
import { SystemStatePanel } from './SystemStatePanel.tsx';
import { ResourcesPanel } from './ResourcesPanel.tsx';
import { KeysPanel } from './KeysPanel.tsx';
import { DebugPanel } from './DebugPanel.tsx';
import AgentLauncherPanel from './AgentLauncherPanel.tsx';
import { ServicesMonitorPanel } from './ServicesMonitorPanel.tsx';
import { AgentMonitoringPanel } from './AgentMonitoringPanel.tsx';
import { LlmMonitorPanel } from './LlmMonitorPanel.tsx';
import { ProcessesPanel } from './Monitoring/processus.panel.tsx';
import { ProjectsPanel } from './Monitoring/projets.panel.tsx';
import { TeamCompositionPanel } from './TeamCompositionPanel.tsx';

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
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: 'transparent',
      color: 'var(--fg, #e2e8f0)',
      fontFamily: 'var(--font-ui, sans-serif)',
      overflow: 'hidden',
      boxSizing: 'border-box',
    }}>
      <style>{`@keyframes mw-spin { to { transform: rotate(360deg); } }`}</style>

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

        <div style={{ display: 'flex', gap: '0.3rem', flex: 1, minHeight: 0 }}>
          <div style={{ flex: 1 }}>
            <SectionCard title=" LLM distant (coûts & tokens)">
              <LlmMonitorPanel />
            </SectionCard>
          </div>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Équipes">
              <TeamCompositionPanel app={app} />
            </SectionCard>
          </div>
        </div>

        <div style={{ fontSize: '0.75rem', fontWeight: '600', color: '#64748b', marginTop: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Monitoring
        </div>

        <div style={{ display: 'flex', gap: '0.3rem', flex: 1, minHeight: 0 }}>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Services">
              <ServicesMonitorPanel app={app} />
            </SectionCard>
          </div>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Processus (top CPU)">
              <ProcessesPanel />
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
            <SectionCard title=" Métriques agents & services">
              <AgentMonitoringPanel />
            </SectionCard>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.3rem', flex: 1, minHeight: 0 }}>
          <div style={{ flex: 1 }}>
            <SectionCard title=" LLM locaux">
              <LocalModelsPanel app={app} />
            </SectionCard>
          </div>
          <div style={{ flex: 1 }}>
            <SectionCard title=" Projets">
              <ProjectsPanel />
            </SectionCard>
          </div>
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