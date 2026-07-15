import React from 'react';
import type { AppApi } from '../useApp.ts';
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';
import { Spinner } from '../components/ui.tsx';
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
      {/* Header */}
      <div style={{
        padding: '1rem 1.5rem',
        borderBottom: '1px solid #334155',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div>
          <h1 style={{ fontSize: '1.25rem', fontWeight: 'bold' }}>ModelWeaver Logithèque</h1>
          <div style={{ fontSize: '0.7rem', color: '#64748b' }}>
            {app.logithequeLoading ? 'Chargement...' : 'Catalogue local + sync distante async'}
          </div>
          <div style={{ fontSize: '0.65rem', color: '#475569', marginTop: '0.1rem', fontFamily: 'monospace' }}>
            {app.appVersion ? `v${app.appVersion}` : 'v…'}
          </div>
        </div>
        <button
          onClick={() => app.withFeedback('load-logitheque', async () => { app.setCatalogueTools([]); await app.refreshInstalled(); await app.loadLogitheque(); })}
          disabled={app.loadingActions['load-logitheque']}
          style={{ padding: '0.4rem 0.8rem', backgroundColor: app.loadingActions['load-logitheque'] ? '#1e293b' : '#334155', color: app.loadingActions['load-logitheque'] ? '#64748b' : '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: app.loadingActions['load-logitheque'] ? 'default' : 'pointer', fontSize: '0.75rem', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
        >
          {app.loadingActions['load-logitheque'] ? <Spinner /> : '↻'} Rafraîchir
        </button>
        <button
          onClick={() => app.withFeedback('install-all', async () => {
            app.addLog('Installation automatique de tous les outils...');
            try { const r = await invoke<any>('install_all_tools'); app.addLog(`Résultat: ${JSON.stringify(r)}`); } catch (e) { app.addLog(`Erreur install all: ${e}`); }
          })}
          disabled={app.loadingActions['install-all']}
          style={{ padding: '0.4rem 0.8rem', backgroundColor: app.loadingActions['install-all'] ? '#064e3b' : '#059669', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: app.loadingActions['install-all'] ? 'default' : 'pointer', fontSize: '0.75rem', fontWeight: '600', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
        >
          {app.loadingActions['install-all'] ? <Spinner /> : '⚡'} Tout installer
        </button>
        <button
          onClick={() => app.setDebug(!app.showDebug)}
          style={{ padding: '0.4rem 0.8rem', backgroundColor: app.showDebug ? '#2563eb' : '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
        >
          🐞 Debug
        </button>
        <button
          onClick={() => { app.setShowKeys(!app.showKeys); if (!app.showKeys) { app.fetchKeys(); app.fetchProviders(); } }}
          style={{ padding: '0.4rem 0.8rem', backgroundColor: app.showKeys ? '#7c3aed' : '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
        >
          🔑 Clés
        </button>
        <button
          onClick={() => {
            const v = !app.showChat;
            app.setShowChat(v);
            app.setShowLocal(false);
            getCurrentWindow().setSize(new LogicalSize(v ? 1380 : 1000, v ? 760 : 700)).catch(() => {});
            if (v) { app.fetchProviders(); app.fetchModels(); }
          }}
          style={{ padding: '0.4rem 0.8rem', backgroundColor: app.showChat ? '#dc2626' : '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
        >
          💬 Chat
        </button>
          <button
            onClick={() => {
              const v = !app.showLocal;
              app.setShowLocal(v);
              app.setShowChat(false);
              if (v) { getCurrentWindow().setSize(new LogicalSize(1100, 760)).catch(() => {}); app.fetchLocalEngines(); }
              else { getCurrentWindow().setSize(new LogicalSize(1000, 700)).catch(() => {}); }
            }}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: app.showLocal ? '#f59e0b' : '#334155', color: '#0f172a', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem', fontWeight: '600' }}
          >
            🖥️ LLM locaux
          </button>
          <button
            onClick={() => {
              const v = !app.showAgents;
              app.setShowAgents(v);
              app.setShowChat(false); app.setShowLocal(false);
              getCurrentWindow().setSize(new LogicalSize(v ? 1200 : 1000, v ? 760 : 700)).catch(() => {});
              if (v) app.fetchAgents();
            }}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: app.showAgents ? '#06b6d4' : '#334155', color: '#0f172a', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem', fontWeight: '600' }}
          >
            🤖 Agents
          </button>
        <button
          onClick={app.toggleFullscreen}
          style={{ padding: '0.4rem 0.6rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
          title="Plein écran"
        >
          {app.isFullscreen ? '🗗' : '⛶'}
        </button>
      </div>

      {app.logithequeError && (
        <div style={{ color: '#fca5a5', padding: '0.5rem 1.5rem', fontSize: '0.8rem' }}>Erreur: {app.logithequeError}</div>
      )}

      {/* Body */}
      <div style={{ flex: 1, display: 'flex', gap: '1rem', padding: '1rem 1.5rem', overflow: 'hidden' }}>

        {app.showChat ? (
          <ChatPanel app={app} />
        ) : app.showAgents ? (
          <AgentsPanel app={app} />
        ) : app.showLocal ? (
          <LocalModelsPanel app={app} />
        ) : (
          <>
        <div style={{ width: '340px', display: 'flex', flexDirection: 'column', gap: '1rem', overflowY: 'auto' }}>
          {/* Left: system state + installed */}
          <SystemStatePanel app={app} />
          <ResourcesPanel app={app} />
          <InstalledToolsPanel app={app} />
        </div>

        {/* Right: catalogue + install queue */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem', overflow: 'hidden' }}>
          <CataloguePanel app={app} />
          <InstallQueuePanel app={app} />
        </div>
          </>
        )}
        {app.showKeys && (
          <KeysPanel app={app} />
        )}
        {app.showDebug && (
          <DebugPanel app={app} />
        )}
      </div>
    </div>
  );
}
