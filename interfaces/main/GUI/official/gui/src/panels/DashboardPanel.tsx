import React from 'react';
import type { AppApi } from '../useApp.ts';
import { invoke } from '@tauri-apps/api/core';
import { Spinner } from '../components/ui.tsx';
import { PanelContainer } from '../components/PanelContainer.tsx';
import { Panel, Group, Separator } from 'react-resizable-panels';
import { ChatPanel } from './ChatPanel.tsx';
import { AgentsPanel } from './AgentsPanel.tsx';
import { LocalModelsPanel } from './LocalModelsPanel.tsx';
import { SystemStatePanel } from './SystemStatePanel.tsx';
import { ResourcesPanel } from './ResourcesPanel.tsx';
import { InstalledToolsPanel } from './InstalledToolsPanel.tsx';
import { CataloguePanel } from './CataloguePanel.tsx';
import { InstallQueuePanel } from './InstallQueuePanel.tsx';
import { KeysPanel } from './KeysPanel.tsx';
import { DebugPanel } from './DebugPanel.tsx';

const PANEL_MAP: Record<string, { title: string; render: (app: AppApi) => React.ReactNode }> = {
  'system-state': { title: 'État du système', render: app => <SystemStatePanel app={app} /> },
  'resources': { title: 'Ressources', render: app => <ResourcesPanel app={app} /> },
  'installed-tools': { title: 'Outils installés', render: app => <InstalledToolsPanel app={app} /> },
  'catalogue': { title: 'Catalogue', render: app => <CataloguePanel app={app} /> },
  'chat': { title: 'Chat', render: app => <ChatPanel app={app} /> },
  'install-queue': { title: "File d'installation", render: app => <InstallQueuePanel app={app} /> },
  'agents': { title: 'Agents', render: app => <AgentsPanel app={app} /> },
  'local-models': { title: 'LLM locaux', render: app => <LocalModelsPanel app={app} /> },
  'keys': { title: 'Clés API', render: app => <KeysPanel app={app} /> },
  'debug': { title: 'Debug', render: app => <DebugPanel app={app} /> },
};

export function DashboardPanel({ app }: { app: AppApi }) {
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

      {/* Top bar */}
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
        <div style={{ flex: 1 }} />
        <button onClick={() => app.withFeedback('load-logitheque', async () => { app.setCatalogueTools([]); await app.refreshInstalled(); await app.loadLogitheque(); })}
          disabled={app.loadingActions['load-logitheque']}
          style={{ padding: '0.25rem 0.6rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.7rem' }}>
          {app.loadingActions['load-logitheque'] ? <Spinner size={10} /> : '↻'} Rafraîchir
        </button>
        <button onClick={app.toggleFullscreen}
          style={{ padding: '0.25rem 0.5rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.7rem' }}>
          {app.isFullscreen ? '🗗' : '⛶'}
        </button>
      </div>

      {app.logithequeError && (
        <div style={{ color: '#fca5a5', padding: '0.3rem 1rem', fontSize: '0.75rem' }}>Erreur: {app.logithequeError}</div>
      )}

      {/* 3-column resizable layout */}
      <Group orientation="horizontal" style={{ flex: 1 }}>
        {/* Left column */}
        <Panel id="left-col" defaultSize="20" minSize="12" maxSize="35">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', padding: '0.3rem', overflowY: 'auto', height: '100%' }}>
            {(app.panelOrder.left || []).map(id => {
              const info = PANEL_MAP[id];
              if (!info) return null;
              return (
                <PanelContainer key={id} id={id} title={info.title} app={app}>
                  {info.render(app)}
                </PanelContainer>
              );
            })}
          </div>
        </Panel>

        <Separator style={{ width: '4px', backgroundColor: '#334155', borderRadius: '2px', margin: '0 2px', cursor: 'col-resize' }} />

        {/* Center column (large) */}
        <Panel id="center-col" defaultSize="60" minSize="20">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', padding: '0.3rem', overflowY: 'auto', height: '100%' }}>
            {(app.panelOrder.center || []).map(id => {
              const info = PANEL_MAP[id];
              if (!info) return null;
              return (
                <PanelContainer key={id} id={id} title={info.title} app={app}>
                  {info.render(app)}
                </PanelContainer>
              );
            })}
          </div>
        </Panel>

        <Separator style={{ width: '4px', backgroundColor: '#334155', borderRadius: '2px', margin: '0 2px', cursor: 'col-resize' }} />

        {/* Right column */}
        <Panel id="right-col" defaultSize="20" minSize="12" maxSize="35">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', padding: '0.3rem', overflowY: 'auto', height: '100%' }}>
            {(app.panelOrder.right || []).map(id => {
              const info = PANEL_MAP[id];
              if (!info) return null;
              return (
                <PanelContainer key={id} id={id} title={info.title} app={app}>
                  {info.render(app)}
                </PanelContainer>
              );
            })}
          </div>
        </Panel>
      </Group>
    </div>
  );
}
