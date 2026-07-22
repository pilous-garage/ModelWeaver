import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner } from '../components/ui.tsx';
import { CatalogTree } from '../components/CatalogTree.tsx';

export function InstalledToolsPanel({ app }: { app: AppApi }) {
  const renderItem = (t: any) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem', borderBottom: '1px solid #334155', paddingBottom: '0.3rem' }}>
      <span style={{ fontWeight: '500' }}>{t.name || t.ref} <span style={{ color: '#6ee7b7' }}>{t.version || ''}</span></span>
      <button
        onClick={async () => await app.handleUninstallTool(t.ref, t.name || t.ref)}
        disabled={app.loadingActions[`uninstall-${t.ref}`]}
        style={{ backgroundColor: '#7f1d1d', color: app.loadingActions[`uninstall-${t.ref}`] ? '#fca5a5' : '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.25rem', padding: '0.1rem 0.5rem', fontSize: '0.7rem', cursor: app.loadingActions[`uninstall-${t.ref}`] ? 'default' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
      >
        {app.loadingActions[`uninstall-${t.ref}`] ? <Spinner size={10} color="#fca5a5" /> : null} Uninstall
      </button>
    </div>
  );

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem', flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.75rem', flexShrink: 0 }}>Outils installés ({app.installedTools.length})</h3>
      {app.logithequeLoading && app.installedTools.length === 0 ? (
        <div style={{ color: '#94a3b8', fontSize: '0.78rem' }}>Chargement…</div>
      ) : app.installedTools.length === 0 ? (
        <div style={{ color: '#94a3b8', fontSize: '0.78rem' }}>Aucun outil détecté</div>
      ) : (
        <CatalogTree
          items={app.installedTools}
          theme="slate"
          storageKey="mw_fold_installed_tools"
          searchPlaceholder="Rechercher un outil installé…"
          getKey={(t: any) => t.ref}
          getSearchText={(t: any) => `${t.name || ''} ${t.ref} ${t.version || ''} ${t.classe || ''} ${t.tool_type || ''}`}
          getGroupPath={(t: any) => {
            const cls = t.classe || t.tool_type || 'Autre';
            const sub = t.tool_type && t.tool_type !== cls ? [t.tool_type] : [];
            return [cls, ...sub];
          }}
          renderItem={renderItem}
        />
      )}
    </div>
  );
}
