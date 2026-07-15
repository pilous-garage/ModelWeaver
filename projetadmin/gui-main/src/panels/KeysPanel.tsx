import React from 'react';
import type { AppApi } from '../useApp.ts';
import { selectStyles } from '../components/ui.tsx';
import { maskKey } from '../lib/helpers.ts';

export function KeysPanel({ app }: { app: AppApi }) {
  return (
    <div style={{ width: '340px', display: 'flex', flexDirection: 'column', gap: '0.75rem', overflow: 'hidden', borderLeft: '1px solid #334155', paddingLeft: '1rem' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600' }}>🔑 Clés API ({app.keysList.length})</h3>
      <div style={{ flex: 1, overflowY: 'auto', backgroundColor: '#1e293b', borderRadius: '0.375rem', border: '1px solid #334155', padding: '0.6rem' }}>
        {/* Ajout d'une clé */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginBottom: '0.75rem', padding: '0.5rem', backgroundColor: '#0f172a', borderRadius: '0.375rem' }}>
          {/* Mode toggle */}
          <div style={{ display: 'flex', gap: '0.3rem' }}>
            <button onClick={() => app.setKeyProviderMode('known')}
              style={{ flex: 1, padding: '0.25rem', fontSize: '0.68rem', backgroundColor: app.keyProviderMode === 'known' ? '#1d4ed8' : '#334155', color: '#e2e8f0', border: '1px solid ' + (app.keyProviderMode === 'known' ? '#3b82f6' : '#475569'), borderRadius: '0.25rem', cursor: 'pointer' }}
            >Provider connu</button>
            <button onClick={() => { app.setKeyProviderMode('new'); app.setKeysNewProvider(''); }}
              style={{ flex: 1, padding: '0.25rem', fontSize: '0.68rem', backgroundColor: app.keyProviderMode === 'new' ? '#1d4ed8' : '#334155', color: '#e2e8f0', border: '1px solid ' + (app.keyProviderMode === 'new' ? '#3b82f6' : '#475569'), borderRadius: '0.25rem', cursor: 'pointer' }}
            >Nouveau provider</button>
          </div>

          {app.keyProviderMode === 'known' ? (
            /* Provider connu : dropdown groupé par type */
            <select
              value={app.keysNewProvider}
              onChange={e => app.setKeysNewProvider(e.target.value)}
              style={{ backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', fontSize: '0.72rem', padding: '0.3rem', outline: 'none' }}
            >
              <option value="">-- Sélectionner un provider --</option>
              {(function() {
                const grouped: Record<string, any[]> = {};
                for (const p of app.providersList) {
                  const t = p.provider_type || 'unknown';
                  (grouped[t] ||= []).push(p);
                }
                const sortedTypes = Object.keys(grouped).sort((a: string, b: string) => a.localeCompare(b));
                return sortedTypes.map((t: string) => (
                  <optgroup key={t} label={t}>
                    {grouped[t].map((p: any) => (
                      <option key={p.ref} value={p.ref}>{p.name}{p.api_type ? ' — ' + p.api_type : ''}</option>
                    ))}
                  </optgroup>
                ));
              })()}
            </select>
          ) : (
            /* Nouveau provider : formulaire */
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
              <input placeholder="Ref (ex: groq)" value={app.newProviderForm.ref}
                onChange={e => app.setNewProviderForm({ ...app.newProviderForm, ref: e.target.value })}
                style={{ padding: '0.3rem', fontSize: '0.7rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', outline: 'none' }}
              />
              <input placeholder="Name (ex: Groq)" value={app.newProviderForm.name}
                onChange={e => app.setNewProviderForm({ ...app.newProviderForm, name: e.target.value })}
                style={{ padding: '0.3rem', fontSize: '0.7rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', outline: 'none' }}
              />
              <select value={app.newProviderForm.provider_type}
                onChange={e => app.setNewProviderForm({ ...app.newProviderForm, provider_type: e.target.value })}
                style={{ backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', fontSize: '0.7rem', padding: '0.3rem', outline: 'none' }}
              >
                <option value="cloud">Cloud</option>
                <option value="local">Local</option>
                <option value="ollama">Ollama</option>
                <option value="builtin">Builtin</option>
              </select>
              <input placeholder="API Type (ex: openai_compatible)" value={app.newProviderForm.api_type}
                onChange={e => app.setNewProviderForm({ ...app.newProviderForm, api_type: e.target.value })}
                style={{ padding: '0.3rem', fontSize: '0.7rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', outline: 'none' }}
              />
              <input placeholder="Website (optionnel)" value={app.newProviderForm.website}
                onChange={e => app.setNewProviderForm({ ...app.newProviderForm, website: e.target.value })}
                style={{ padding: '0.3rem', fontSize: '0.7rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', outline: 'none' }}
              />
            </div>
          )}

          <input placeholder="Clé API" value={app.keysNewValue}
            onChange={e => app.setKeysNewValue(e.target.value)}
            style={{ padding: '0.3rem 0.5rem', fontSize: '0.72rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', outline: 'none' }}
          />
          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
            <select value={app.keysNewTag}
              onChange={e => app.setKeysNewTag(e.target.value as 'free' | 'paid')}
              style={{ flex: 1, ...selectStyles, fontSize: '0.72rem', padding: '0.3rem' }}
            >
              <option value="free">Free</option>
              <option value="paid">Paid</option>
            </select>
            <button onClick={app.keyProviderMode === 'known' ? app.handleSetKey : app.handleAddProvider}
              style={{ padding: '0.3rem 0.7rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.72rem', fontWeight: '600' }}
            >+</button>
          </div>
          {app.keyMsg ? (
            <div style={{ fontSize: '0.66rem', color: app.keyMsg.startsWith('✅') ? '#6ee7b7' : '#fca5a5', marginTop: '0.3rem' }}>{app.keyMsg}</div>
          ) : null}
        </div>
        {/* Liste des clés */}
        {app.keysList.length === 0 ? (
          <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucune clé enregistrée</div>
        ) : (
          app.keysList.map((k: any) => (
            <div key={k.ref} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.35rem 0', borderBottom: '1px solid #334155', fontSize: '0.72rem', opacity: k.locked ? 0.55 : 1 }}>
              <div style={{ overflow: 'hidden' }}>
                <div style={{ fontWeight: '600', fontSize: '0.75rem' }}>{k.provider_ref || k.provider_name}</div>
                <div style={{ color: '#94a3b8', fontFamily: 'monospace', fontSize: '0.7rem' }}>
                  {k.key_display || maskKey(k.api_key || '')}
                  <span style={{ marginLeft: '0.4rem', fontSize: '0.6rem', backgroundColor: k.tag === 'free' ? '#065f46' : '#7c2d12', borderRadius: '0.2rem', padding: '0.05rem 0.3rem', color: k.tag === 'free' ? '#6ee7b7' : '#fdba74' }}>{k.tag}</span>
                  {k.locked && <span style={{ marginLeft: '0.4rem', fontSize: '0.6rem', backgroundColor: '#7c2d12', borderRadius: '0.2rem', padding: '0.05rem 0.3rem', color: '#fdba74' }}>🔒 verrouillée</span>}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                {/* Slider lock / unlock */}
                <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }} title={k.locked ? 'Déverrouiller' : 'Verrouiller'}>
                  <input
                    type="checkbox"
                    checked={!k.locked}
                    onChange={() => app.handleToggleLock(k.ref, k.locked)}
                    style={{ cursor: 'pointer', width: '0.9rem', height: '0.9rem', accentColor: '#3b82f6' }}
                  />
                </label>
                <button
                  onClick={() => app.handleDeleteKey(k.provider_ref)}
                  style={{ padding: '0.15rem 0.4rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.25rem', cursor: 'pointer', fontSize: '0.65rem' }}
                  >✕</button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Fetch modèles : uniquement providers ayant une clé API */}
      <div style={{ marginTop: '0.75rem', borderTop: '1px solid #334155', paddingTop: '0.6rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
          <button
            onClick={app.fetchModels}
            disabled={app.modelsLoading}
            style={{ padding: '0.25rem 0.6rem', backgroundColor: app.modelsLoading ? '#1e293b' : '#3b82f6', color: app.modelsLoading ? '#64748b' : 'white', border: 'none', borderRadius: '0.3rem', cursor: app.modelsLoading ? 'default' : 'pointer', fontSize: '0.7rem', fontWeight: '600' }}
          >{app.modelsLoading ? 'Chargement…' : '🔄 Fetch modèles'}</button>
          <span style={{ fontSize: '0.62rem', color: '#64748b' }}>providers avec clé uniquement</span>
        </div>
        {app.modelsList.length === 0 ? (
          <div style={{ fontSize: '0.68rem', color: '#94a3b8' }}>Aucun modèle (fournissez une clé API pour un provider)</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', maxHeight: '220px', overflowY: 'auto' }}>
            {(() => {
              const byProv: Record<string, any[]> = {};
              for (const m of app.modelsList) {
                const pr = m.provider_ref || m.provider_name || 'unknown';
                (byProv[pr] ||= []).push(m);
              }
              return Object.keys(byProv).sort((a, b) => a.localeCompare(b)).map((pr) => (
                <div key={pr} style={{ backgroundColor: '#0f172a', borderRadius: '0.3rem', padding: '0.4rem' }}>
                  <div style={{ fontSize: '0.68rem', fontWeight: '600', color: '#93c5fd', marginBottom: '0.25rem' }}>{pr} ({byProv[pr].length})</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                    {byProv[pr].map((m, i) => (
                      <span key={i} title={m.provider_model_name || m.ref} style={{ fontSize: '0.6rem', backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '0.25rem', padding: '0.1rem 0.35rem', color: '#cbd5e1' }}>{m.name || m.ref}</span>
                    ))}
                  </div>
                </div>
              ));
            })()}
          </div>
        )}
      </div>
    </div>
  );
}
