import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner } from '../components/ui.tsx';

export function InstallQueuePanel({ app }: { app: AppApi }) {
  return (
    <div style={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '0.375rem', overflow: 'hidden' }}>
      <button
        onClick={() => app.setInstallListOpen(!app.installListOpen)}
        style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.6rem 1rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: 'none', cursor: 'pointer', fontSize: '0.8rem', fontWeight: '600' }}
      >
        <span>File d'installation ({app.installQueue.filter(q => q.status === 'installed' || q.status === 'removed').length}/{app.installQueue.length})</span>
        <span style={{ transform: app.installListOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▾</span>
      </button>
      {app.installListOpen && (
        <div style={{ padding: '0 1rem 0.75rem' }}>
          {app.installQueue.length === 0 ? (
            <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun outil en attente — ajoutez des outils du catalogue</div>
          ) : (
            <>
              {app.installQueue.map((q) => {
                const badge = q.status === 'running' ? { t: '⏳', c: '#fbbf24', l: 'En cours' }
                  : q.status === 'queued' ? { t: '🕓', c: '#93c5fd', l: 'En attente' }
                  : q.status === 'installed' ? { t: '✓', c: '#6ee7b7', l: 'Installé' }
                  : q.status === 'removed' ? { t: '✓', c: '#6ee7b7', l: 'Retiré' }
                  : q.status === 'failed' ? { t: '✗', c: '#f87171', l: 'Échec' }
                  : q.status === 'cancelled' ? { t: '⊘', c: '#fbbf24', l: 'Annulé' }
                  : { t: '•', c: '#94a3b8', l: q.status };
                const canCancel = q.status === 'queued' || q.status === 'running';
                return (
                  <div key={q.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.3rem 0', borderBottom: '1px solid #334155', fontSize: '0.75rem' }}>
                    <span style={{ color: badge.c, fontWeight: '700', width: '1.2rem' }}>{badge.t}</span>
                    <span style={{ fontWeight: '500' }}>{q.name}</span>
                    <span style={{ color: '#64748b', fontSize: '0.7rem' }}>{badge.l}</span>
                    {canCancel && (
                      <button
                        onClick={() => app.handleCancelInstall(q.id, q.name, q.ref)}
                        style={{ marginLeft: 'auto', padding: '0.15rem 0.5rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.68rem' }}
                      >Annuler</button>
                    )}
                    {!canCancel && q.log && (q.status === 'failed') && (
                      <span
                        title={q.log}
                        style={{ marginLeft: 'auto', fontSize: '0.62rem', color: '#fca5a5', maxWidth: '55%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      >{q.log.replace(/\\n/g, ' ').slice(0, 80)}</span>
                    )}
                  </div>
                );
              })}
              <button
                onClick={app.handleClearQueue}
                disabled={app.loadingActions['clear-queue']}
                style={{ marginTop: '0.5rem', padding: '0.25rem 0.6rem', backgroundColor: app.loadingActions['clear-queue'] ? '#1e293b' : '#334155', color: app.loadingActions['clear-queue'] ? '#64748b' : '#cbd5e1', border: '1px solid #475569', borderRadius: '0.3rem', cursor: app.loadingActions['clear-queue'] ? 'default' : 'pointer', fontSize: '0.7rem', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
              >{app.loadingActions['clear-queue'] ? <Spinner size={9} color="#64748b" /> : null}Vider la file (terminés)</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
