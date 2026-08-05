import React from 'react';
import type { AppApi } from '../useApp.ts';
import { SystemStatePanel } from './SystemStatePanel.tsx';
import { ResourcesPanel } from './ResourcesPanel.tsx';

// DashboardPanel : panel SIMPLE d'état (plus le conteneur plein écran avec
// son propre MenuBar + PanelTreeRenderer imbriqué, qui cassait les splits
// du nouveau système). Affiche l'état système + ressources côte à côte.

export function DashboardPanel({ app }: { app: AppApi }) {
  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'row',
      backgroundColor: 'transparent',
      color: 'var(--fg, #e2e8f0)',
      fontFamily: 'var(--font-ui, sans-serif)',
      gap: '0.5rem',
      padding: '0.5rem',
      overflow: 'hidden',
      boxSizing: 'border-box',
    }}>
      <div style={{ flex: 1, minWidth: 0, overflow: 'auto', backgroundColor: '#1e293b', borderRadius: '0.4rem', border: '1px solid #334155', padding: '0.5rem' }}>
        <div style={{ fontWeight: 600, marginBottom: '0.4rem', fontSize: '0.78rem' }}>État système</div>
        <SystemStatePanel app={app} />
      </div>
      <div style={{ flex: 1, minWidth: 0, overflow: 'auto', backgroundColor: '#1e293b', borderRadius: '0.4rem', border: '1px solid #334155', padding: '0.5rem' }}>
        <div style={{ fontWeight: 600, marginBottom: '0.4rem', fontSize: '0.78rem' }}>Ressources</div>
        <ResourcesPanel app={app} />
      </div>
    </div>
  );
}
