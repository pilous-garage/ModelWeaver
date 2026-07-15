import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner } from '../components/ui.tsx';
import { groupByClass } from '../lib/helpers.ts';

export function CataloguePanel({ app }: { app: AppApi }) {
  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem', flex: 1, overflowY: 'auto' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.75rem' }}>Catalogue d'outils ({app.catalogueTools.length})</h3>
      {app.catalogueTools.length === 0 ? (
        <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Chargement du catalogue…</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {groupByClass(app.catalogueTools).map(([classe, tools]) => (
            <div key={classe}>
              <button
                onClick={() => app.toggleFold(classe)}
                style={{ width: '100%', textAlign: 'left', backgroundColor: '#0f172a', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', padding: '0.35rem 0.5rem', fontSize: '0.78rem', fontWeight: '600', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <span>{app.foldedClasses[classe] ? '▸' : '▾'} {classe}</span>
                <span style={{ color: '#64748b', fontWeight: '400' }}>{tools.length}</span>
              </button>
              {!app.foldedClasses[classe] && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '0.5rem' }}>
                  {tools.map((t: any) => {
            const inst = app.isInstalled(t.ref);
            const queued = app.isQueued(t.ref);
            return (
              <div key={t.ref} style={{ backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.375rem', padding: '0.75rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ maxWidth: '70%' }}>
                  <div style={{ fontWeight: '600', fontSize: '0.85rem' }}>{t.name} <span style={{ fontSize: '0.6rem', color: '#64748b' }}>({t.ref})</span></div>
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>{t.description}</div>
                  <div style={{ marginTop: '0.25rem' }}>
                    <span style={{ fontSize: '0.6rem', backgroundColor: '#0c4a6e', border: '1px solid #0ea5e9', borderRadius: '0.25rem', padding: '0.05rem 0.35rem', color: '#7dd3fc' }}>{t.classe || t.tool_type}</span>
                    <span style={{ fontSize: '0.6rem', backgroundColor: '#1e293b', border: '1px solid #475569', borderRadius: '0.25rem', padding: '0.05rem 0.35rem', color: '#cbd5e1', marginLeft: '0.3rem' }}>{t.install_method}</span>
                  </div>
                </div>
                <div>
                  {inst ? (
                    <span style={{ color: '#6ee7b7', fontSize: '0.75rem', fontWeight: '600' }}>✓ Installé</span>
                  ) : (() => {
                    const j = app.queueJob(t.ref);
                    const added = app.loadingActions[`install-${t.ref}`] || app.pendingInstalls[t.ref] === true || (j != null && (j.status === 'queued' || j.status === 'running'));
                    if (inst || (j != null && (j.status === 'installed' || j.status === 'removed'))) {
                      const txt = j != null && j.status === 'removed' ? '✓ Retiré' : '✓ Installé';
                      return (
                        <span style={{ color: '#6ee7b7', fontSize: '0.75rem', fontWeight: '600' }}>{txt}</span>
                      );
                    }
                    if (added) {
                      return (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
                          <button
                            disabled
                            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', padding: '0.4rem 0.7rem', backgroundColor: '#1e3a8a', color: '#bfdbfe', border: 'none', borderRadius: '0.375rem', cursor: 'default', fontSize: '0.72rem', fontWeight: '500', opacity: 0.9 }}
                          ><Spinner size={10} color="#bfdbfe" />added</button>
                          {j != null && (j.status === 'queued' || j.status === 'running') && (
                            <button
                              onClick={() => app.handleCancelInstall(j.id, t.name, t.ref)}
                              disabled={app.loadingActions[`cancel-${j.id}`]}
                              style={{ padding: '0.25rem 0.55rem', backgroundColor: app.loadingActions[`cancel-${j.id}`] ? '#4c0519' : '#7f1d1d', color: '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.3rem', cursor: app.loadingActions[`cancel-${j.id}`] ? 'default' : 'pointer', fontSize: '0.7rem', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                            >{app.loadingActions[`cancel-${j.id}`] ? <Spinner size={9} color="#fecaca" /> : null}Annuler</button>
                          )}
                        </span>
                      );
                    }
                    return (
                      <button
                        onClick={() => app.handleAddToInstallList(t.ref, t.name)}
                        disabled={app.loadingActions[`install-${t.ref}`]}
                        style={{ padding: '0.4rem 0.7rem', backgroundColor: app.loadingActions[`install-${t.ref}`] ? '#1e3a8a' : '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: app.loadingActions[`install-${t.ref}`] ? 'default' : 'pointer', fontSize: '0.72rem', fontWeight: '500', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                      >
                        {app.loadingActions[`install-${t.ref}`] ? <Spinner size={10} color="#fff" /> : null}+ Add to install list
                      </button>
                    );
                  })()}
                </div>
              </div>
            );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
