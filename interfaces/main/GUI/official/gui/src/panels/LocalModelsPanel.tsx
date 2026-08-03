import React, { useState } from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner } from '../components/ui.tsx';

export function LocalModelsPanel({ app }: { app: AppApi }) {
  const [expandedGroup, setExpandedGroup] = useState<Record<string, boolean>>({});
  const [hfPage, setHfPage] = useState(0);

  const toggleGroup = (fmt: string) => {
    setExpandedGroup(prev => ({ ...prev, [fmt]: !prev[fmt] }));
  };

  const grouped = app.localGrouped;
   const groups = grouped?.groups || [];
   const localGguf = grouped?.local_gguf_files || [];
   const hw = app.localHardware;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.8rem', overflow: 'hidden' }}>
      {/* En-tête */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
        <h3 style={{ fontSize: '1rem', fontWeight: '600' }}>🖥️ Modèles locaux</h3>
        <button onClick={app.fetchGroupedEngines} disabled={app.localLoading}
          style={{ padding: '0.3rem 0.7rem', fontSize: '0.72rem', backgroundColor: app.localLoading ? '#1e293b' : '#2563eb', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: app.localLoading ? 'default' : 'pointer' }}>
          {app.localLoading ? <Spinner size={12} color="#64748b" /> : '↻ Actualiser'}
        </button>
      </div>

      {/* Messages */}
      {app.localMsg && <div style={{ color: '#fca5a5', fontSize: '0.75rem' }}>{app.localMsg}</div>}

      {/* Groupes par format */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
        {groups.length === 0 && !app.localLoading && (
          <div style={{ color: '#94a3b8', fontSize: '0.8rem', padding: '1rem', backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155' }}>
            Aucun moteur détecté. Installez Ollama ou llama.cpp.
          </div>
        )}

        {groups.map((g: any) => (
          <div key={g.format} style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', overflow: 'hidden' }}>
             <div style={{ padding: '0.6rem 0.8rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', borderBottom: expandedGroup[g.format] ? '1px solid #334155' : 'none' }}
               onClick={() => toggleGroup(g.format)}>
               <span style={{ fontSize: '0.8rem', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase' }}>
                 {g.format}
               </span>
               <span style={{ fontSize: '0.68rem', color: '#64748b' }}>
                 {g.engines.length} moteur{g.engines.length > 1 ? 's' : ''} · {expandedGroup[g.format] ? '▼' : '▶'}
               </span>
             </div>
             {expandedGroup[g.format] && (
               <div style={{ padding: '0.5rem 0.8rem', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                 {g.engines.map((e: any) => (
                   <EngineCard key={e.ref} engine={e} hwModes={hw?.modes?.[e.ref]?.hardware_modes || []}
                     onStart={app.handleStartModel} onStopModel={app.handleStopModel}
                    onCheckResources={app.handleCheckResources}
                    localBusy={app.localBusy} localHwMode={app.localHwMode} setLocalHwMode={app.setLocalHwMode} />
                ))}
                {g.local_gguf && g.local_gguf.length > 0 && (
                  <div style={{ marginTop: '0.3rem' }}>
                    <div style={{ fontSize: '0.68rem', color: '#64748b', marginBottom: '0.2rem' }}>GGUF locaux :</div>
                    {g.local_gguf.map((f: any) => (
                      <span key={f.ref} style={{ fontSize: '0.64rem', backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.25rem', padding: '0.1rem 0.4rem', color: '#93c5fd', marginRight: '0.3rem' }}>
                        {f.name} ({f.size_gb} Go)
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {/* Fichiers GGUF locaux (hors groupes moteurs) */}
        {localGguf.length > 0 && (
          <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.6rem 0.8rem' }}>
            <div style={{ fontSize: '0.72rem', fontWeight: '600', color: '#94a3b8', marginBottom: '0.3rem' }}>📁 Fichiers GGUF téléchargés</div>
            {localGguf.map((f: any) => (
              <div key={f.path} style={{ fontSize: '0.68rem', color: '#94a3b8', display: 'flex', justifyContent: 'space-between' }}>
                <span>{f.name}</span><span>{f.size_gb} Go · {f.repo}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Catalogue HuggingFace */}
      <div style={{ flexShrink: 0, borderTop: '1px solid #334155', paddingTop: '0.6rem' }}>
        <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.4rem' }}>
          <input type="text" value={app.hfQuery} placeholder="Rechercher un modèle GGUF…"
            onChange={e => app.setHfQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && app.hfDoSearch(app.hfQuery)}
            style={{ flex: 1, padding: '0.3rem 0.5rem', fontSize: '0.72rem', backgroundColor: '#0f172a', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', outline: 'none' }} />
          <button onClick={() => app.hfDoSearch(app.hfQuery)} disabled={app.hfLoading}
            style={{ padding: '0.3rem 0.6rem', fontSize: '0.72rem', backgroundColor: '#7c3aed', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: 'pointer' }}>
            {app.hfLoading ? <Spinner size={12} color="#fff" /> : '🔍'}
          </button>
        </div>

        {app.hfResults.length > 0 && (
          <div style={{ maxHeight: '12rem', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            {app.hfResults.map((m: any) => (
              <div key={m.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.3rem 0.5rem', backgroundColor: '#0f172a', borderRadius: '0.3rem', border: '1px solid #334155', fontSize: '0.68rem' }}>
                <div>
                  <span style={{ color: '#e2e8f0', fontWeight: '500' }}>{m.modelId}</span>
                  <span style={{ color: '#64748b', marginLeft: '0.4rem' }}>↓ {m.downloads.toLocaleString()}</span>
                </div>
                <div style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
                   <select value={app.hfAssociateEngine} onChange={e => app.setHfAssociateEngine(e.target.value)}
                     style={{ fontSize: '0.64rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.2rem', padding: '0.1rem 0.3rem' }}>
                     <option value="llamacpp">llama.cpp</option>
                     <option value="ollama">Ollama</option>
                   </select>
                  <button onClick={() => app.hfDoDownload(m.id, '')} disabled={!!app.hfDownloading[m.id]}
                    style={{ padding: '0.15rem 0.4rem', fontSize: '0.64rem', backgroundColor: '#059669', color: 'white', border: 'none', borderRadius: '0.2rem', cursor: 'pointer' }}>
                    {app.hfDownloading[m.id] ? '…' : '⬇'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {app.hfDownloadStatus && (
          <div style={{ fontSize: '0.64rem', color: '#64748b', marginTop: '0.2rem' }}>
            {app.hfDownloadStatus.done ? '✅ Téléchargement terminé' :
              app.hfDownloadStatus.error ? `⚠️ ${app.hfDownloadStatus.error}` :
                `⏳ ${(app.hfDownloadStatus.bytes || 0) / (1024*1024) | 0} Mo / ${(app.hfDownloadStatus.total || 0) / (1024*1024) | 0} Mo`}
          </div>
        )}

        {app.hfLocalModels.length > 0 && (
          <div style={{ marginTop: '0.3rem' }}>
            <div style={{ fontSize: '0.68rem', color: '#64748b', marginBottom: '0.2rem' }}>Modèles locaux :</div>
            {app.hfLocalModels.map((m: any) => (
              <div key={m.path} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.64rem', padding: '0.15rem 0', color: '#94a3b8' }}>
                <span>{m.filename} ({m.size_gb} Go)</span>
                <button onClick={() => app.hfDoAssociate(m.repo || '', m.filename, app.hfAssociateEngine)}
                  style={{ padding: '0.1rem 0.3rem', fontSize: '0.6rem', backgroundColor: '#1e40af', color: 'white', border: 'none', borderRadius: '0.2rem', cursor: 'pointer' }}>
                  Associer
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function EngineCard({ engine, hwModes, onStart, onStopModel, onCheckResources, localBusy, localHwMode, setLocalHwMode }: any) {
  const hw = localHwMode[engine.ref] || hwModes[0] || 'cpu';
  const canStart = engine.running === false && engine.headless !== false;

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.4rem', padding: '0.4rem 0', borderBottom: '1px solid #1e293b' }}>
      <div style={{ flex: 1, minWidth: '12rem' }}>
        <span style={{ fontSize: '0.75rem', fontWeight: '500', color: '#e2e8f0' }}>{engine.name}</span>
        <span style={{ fontSize: '0.64rem', color: '#64748b', marginLeft: '0.4rem' }}>port {engine.port}</span>
        {engine.error && <span style={{ color: '#fca5a5', fontSize: '0.64rem', marginLeft: '0.3rem' }}>⚠️ {engine.error}</span>}
        <div style={{ fontSize: '0.6rem', color: '#475569', marginTop: '0.1rem' }}>
          {engine.hardware_modes?.join(' / ') || '—'}
        </div>
      </div>

      {/* Dropdown matériel */}
      <select value={hw} onChange={e => setLocalHwMode(prev => ({ ...prev, [engine.ref]: e.target.value }))}
        disabled={!canStart}
        style={{ fontSize: '0.64rem', backgroundColor: '#0f172a', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.2rem', padding: '0.1rem 0.3rem' }}>
        {hwModes.map((m: string) => (
          <option key={m} value={m}>{m === 'cpu_gpu' ? 'CPU+GPU' : m === 'cpu' ? 'CPU' : m === 'gpu' ? 'GPU' : m}</option>
        ))}
      </select>

      {/* Boutons */}
      {engine.running ? (
        <>
          <button onClick={() => onStopModel(engine.ref, engine.models?.[0]?.ref || '')} disabled={localBusy === engine.ref}
            style={{ padding: '0.2rem 0.5rem', fontSize: '0.68rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: 'none', borderRadius: '0.3rem', cursor: 'pointer' }}>
            Arrêter
          </button>
          {engine.models.map((m: any) => (
            <button key={m.ref} onClick={() => onCheckResources(m.ref)} disabled={localBusy === engine.ref}
              style={{ padding: '0.2rem 0.4rem', fontSize: '0.64rem', backgroundColor: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: '0.3rem', cursor: 'pointer' }}>
              RAM?
            </button>
          ))}
        </>
      ) : (
        <button onClick={() => {
          const modelRef = engine.models?.[0]?.ref || `local:${engine.name.toLowerCase().replace(/[^a-z0-9]/g, '-')}`;
          onStart(engine.ref, modelRef, hw);
        }} disabled={localBusy === engine.ref || !canStart}
          style={{ padding: '0.2rem 0.5rem', fontSize: '0.68rem', backgroundColor: '#059669', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: canStart ? 'pointer' : 'default' }}>
          Démarrer
        </button>
      )}
    </div>
  );
}