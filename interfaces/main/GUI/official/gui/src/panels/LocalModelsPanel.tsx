import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner } from '../components/ui.tsx';

export function LocalModelsPanel({ app }: { app: AppApi }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
        <h3 style={{ fontSize: '1rem', fontWeight: '600' }}>🖥️ Moteurs LLM locaux</h3>
        <button onClick={app.fetchLocalEngines} disabled={app.localLoading}
          style={{ padding: '0.3rem 0.7rem', fontSize: '0.72rem', backgroundColor: app.localLoading ? '#1e293b' : '#2563eb', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: app.localLoading ? 'default' : 'pointer' }}>
          {app.localLoading ? <Spinner size={12} color="#64748b" /> : '↻ Actualiser'}
        </button>
      </div>
      {app.localMsg && <div style={{ color: '#fca5a5', fontSize: '0.75rem' }}>{app.localMsg}</div>}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
        {app.localEngines.length === 0 && !app.localLoading && (
          <div style={{ color: '#94a3b8', fontSize: '0.8rem', padding: '1rem', backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155' }}>
            Aucun moteur détecté. Installez Ollama pour tester (le démarrage headless est géré par ModelWeaver).
          </div>
        )}
        {app.localEngines.map((e: any) => (
          <div key={e.ref} style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.8rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontSize: '0.85rem', fontWeight: '600', color: '#e2e8f0' }}>{e.name}</div>
                <div style={{ fontSize: '0.68rem', color: '#64748b' }}>
                  port {e.port} · {e.api_type}
                  {e.error && <span style={{ color: '#fca5a5' }}> · ⚠️ {e.error}</span>}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{ fontSize: '0.7rem', padding: '0.15rem 0.5rem', borderRadius: '0.25rem', backgroundColor: e.running ? '#064e3b' : '#334155', color: e.running ? '#6ee7b7' : '#94a3b8' }}>
                  {e.running ? '● actif' : '○ arrêté'}
                </span>
                <button
                  onClick={() => app.handleLocalToggle(e.ref, e.running)}
                  disabled={app.localBusy === e.ref || (e.running === false && !e.headless)}
                  style={{ padding: '0.3rem 0.7rem', fontSize: '0.72rem', fontWeight: '600', borderRadius: '0.3rem', border: 'none', cursor: (app.localBusy === e.ref || (e.running === false && !e.headless)) ? 'default' : 'pointer', backgroundColor: e.running ? '#7f1d1d' : '#059669', color: e.running ? '#fecaca' : 'white' }}
                >
                  {app.localBusy === e.ref ? <Spinner size={12} color="#94a3b8" /> : e.running ? 'Arrêter' : 'Démarrer'}
                </button>
              </div>
            </div>
            {e.models && e.models.length > 0 && (
              <div style={{ marginTop: '0.6rem', display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                {e.models.map((m: any) => (
                  <span key={m.ref} style={{ fontSize: '0.66rem', backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.25rem', padding: '0.1rem 0.45rem', color: '#93c5fd' }}>{m.name}</span>
                ))}
              </div>
            )}
            {e.running && (!e.models || e.models.length === 0) && (
              <div style={{ marginTop: '0.5rem', fontSize: '0.7rem', color: '#64748b' }}>Aucun modèle détecté (pull avec la CLI du moteur).</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
