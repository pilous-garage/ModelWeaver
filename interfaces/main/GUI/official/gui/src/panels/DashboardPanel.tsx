import React, { useState } from 'react';
import type { AppApi, PanelGroupData } from '../useApp.ts';
import { invoke } from '@tauri-apps/api/core';
import { Spinner } from '../components/ui.tsx';
import { TabbedPanel } from '../components/TabbedPanel.tsx';
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

const PANEL_RENDER: Record<string, (app: AppApi) => React.ReactNode> = {
  'system-state': app => <SystemStatePanel app={app} />,
  'resources': app => <ResourcesPanel app={app} />,
  'installed-tools': app => <InstalledToolsPanel app={app} />,
  'catalogue': app => <CataloguePanel app={app} />,
  'chat': app => <ChatPanel app={app} />,
  'install-queue': app => <InstallQueuePanel app={app} />,
  'agents': app => <AgentsPanel app={app} />,
  'local-models': app => <LocalModelsPanel app={app} />,
  'keys': app => <KeysPanel app={app} />,
  'debug': app => <DebugPanel app={app} />,
};

function ColumnPanel({ app, col, groups }: { app: AppApi; col: 'left' | 'center' | 'right'; groups: PanelGroupData[] }) {
  const [dragOver, setDragOver] = useState(false);

  const handleDragOver = (e: React.DragEvent) => {
    const raw = e.dataTransfer.types.includes('application/mw-tab');
    if (raw) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      setDragOver(true);
    }
  };

  const handleDragLeave = () => setDragOver(false);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup } = JSON.parse(raw);
    if (groups.some(g => g.id === fromGroup)) return;
    app.moveTabToNewGroup(tabId, fromGroup, col);
  };

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        padding: '0.3rem',
        overflow: 'hidden',
        height: '100%',
        minHeight: 0,
        border: dragOver ? '2px dashed #60a5fa' : '2px solid transparent',
        borderRadius: '0.5rem',
        transition: 'border-color 0.15s',
      }}
    >
      {groups.map(group => (
        <TabbedPanel key={group.id} group={group} column={col} app={app}>
          {tabId => {
            const render = PANEL_RENDER[tabId];
            return render ? render(app) : <div style={{ color: '#94a3b8' }}>Panneau inconnu: {tabId}</div>;
          }}
        </TabbedPanel>
      ))}
    </div>
  );
}

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

      {/* 3-column resizable layout with tabbed groups */}
      <Group orientation="horizontal" style={{ flex: 1 }}>
        <Panel id="left-col" defaultSize="20" minSize="12" maxSize="35">
          <ColumnPanel app={app} col="left" groups={app.panelGroups.left} />
        </Panel>

        <Separator style={{ width: '4px', backgroundColor: '#334155', borderRadius: '2px', margin: '0 2px', cursor: 'col-resize' }} />

        <Panel id="center-col" defaultSize="60" minSize="20">
          <ColumnPanel app={app} col="center" groups={app.panelGroups.center} />
        </Panel>

        <Separator style={{ width: '4px', backgroundColor: '#334155', borderRadius: '2px', margin: '0 2px', cursor: 'col-resize' }} />

        <Panel id="right-col" defaultSize="20" minSize="12" maxSize="35">
          <ColumnPanel app={app} col="right" groups={app.panelGroups.right} />
        </Panel>
      </Group>
    </div>
  );
}
