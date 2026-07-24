import React from 'react';
import type { AppApi } from '../useApp.ts';
import { MenuBar } from '../components/MenuBar.tsx';
import { PanelTreeRenderer } from '../components/PanelTreeRenderer.tsx';
import { AgentsPanel } from './AgentsPanel.tsx';
import { LocalModelsPanel } from './LocalModelsPanel.tsx';
import { SystemStatePanel } from './SystemStatePanel.tsx';
import { ResourcesPanel } from './ResourcesPanel.tsx';
import { KeysPanel } from './KeysPanel.tsx';
import { DebugPanel } from './DebugPanel.tsx';

const PANEL_RENDER: Record<string, (app: AppApi) => React.ReactNode> = {
  'system-state': app => <SystemStatePanel app={app} />,
  'resources': app => <ResourcesPanel app={app} />,
  'agents': app => <AgentsPanel app={app} />,
  'local-models': app => <LocalModelsPanel app={app} />,
  'keys': app => <KeysPanel app={app} />,
  'debug': app => <DebugPanel app={app} />,
};

export function DashboardPanel({ app }: { app: AppApi }) {
  const renderTab = React.useCallback(
    (tabId: string) => {
      const fn = PANEL_RENDER[tabId];
      return fn ? fn(app) : <div style={{ color: '#94a3b8' }}>Panneau inconnu: {tabId}</div>;
    },
    [app],
  );

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

      {/* Menu bar */}
      <MenuBar app={app} />

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
        <button onClick={app.toggleFullscreen}
          style={{ padding: '0.25rem 0.5rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.7rem' }}>
          {app.isFullscreen ? '🗗' : '⛶'}
        </button>
      </div>

      {/* Tree-based layout */}
      <div style={{ flex: 1, padding: '0.3rem', overflow: 'hidden', display: 'flex' }}>
        <PanelTreeRenderer node={app.panelTree} app={app} renderTab={renderTab} />
      </div>
