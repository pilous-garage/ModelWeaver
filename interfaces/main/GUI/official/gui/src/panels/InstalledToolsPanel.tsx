import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner } from '../components/ui.tsx';
import { groupByClass } from '../lib/helpers.ts';

export function InstalledToolsPanel({ app }: { app: AppApi }) {
  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.75rem' }}>Outils installés ({app.installedTools.length})</h3>
      {app.logithequeLoading && app.installedTools.length === 0 ? (
        <div style={{ color: '#94a3b8', fontSize: '0.78rem' }}>Chargement…</div>
      ) : app.installedTools.length === 0 ? (
        <div style={{ color: '#94a3b8', fontSize: '0.78rem' }}>Aucun outil détecté</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {groupByClass(app.installedTools).map(([classe, tools]) => (
            <div key={classe}>
              <button
                onClick={() => app.toggleFold(classe)}
                style={{ width: '100%', textAlign: 'left', backgroundColor: '#0f172a', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', padding: '0.35rem 0.5rem', fontSize: '0.78rem', fontWeight: '600', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <span>{app.foldedClasses[classe] ? '▸' : '▾'} {classe}</span>
                <span style={{ color: '#64748b', fontWeight: '400' }}>{tools.length}</span>
              </button>
              {!app.foldedClasses[classe] && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginTop: '0.4rem', paddingLeft: '0.3rem' }}>
                  {tools.map((t: any) => (
            <div key={t.ref} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem', borderBottom: '1px solid #334155', paddingBottom: '0.3rem' }}>
              <span style={{ fontWeight: '500' }}>{t.name || t.ref} <span style={{ color: '#6ee7b7' }}>{t.version || ''}</span></span>
              <button
                onClick={async () => await app.handleUninstallTool(t.ref, t.name || t.ref)}
                disabled={app.loadingActions[`uninstall-${t.ref}`]}
                style={{ backgroundColor: '#7f1d1d', color: app.loadingActions[`uninstall-${t.ref}`] ? '#fca5a5' : '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.25rem', padding: '0.1rem 0.5rem', fontSize: '0.7rem', cursor: app.loadingActions[`uninstall-${t.ref}`] ? 'default' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
              >
                {app.loadingActions[`uninstall-${t.ref}`] ? <Spinner size={10} color="#fca5a5" /> : null} Uninstall
              </button>
            </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
