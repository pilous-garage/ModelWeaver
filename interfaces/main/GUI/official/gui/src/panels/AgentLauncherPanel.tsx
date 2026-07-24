import React, { useState } from 'react';
import type { AppApi } from '../useApp.ts';
import { invoke } from '../bridge.ts';

const SOLO_AGENTS = [
  { name: 'codeur', role: 'codeur', desc: 'Génère du code à partir d\'une description' },
  { name: 'analyse', role: 'chercheur', desc: 'Analyse un sujet et produit un rapport' },
  { name: 'test_runner', role: 'test_runner', desc: 'Exécute et vérifie du code' },
  { name: 'relecteur', role: 'relecteur', desc: 'Relecture et critique de contenu' },
  { name: 'debugger', role: 'debugger', desc: 'Diagnostic et correction de bugs' },
  { name: 'documentaliste', role: 'documentaliste', desc: 'Recherche et rédaction documentaire' },
  { name: 'planificateur', role: 'planificateur', desc: 'Décomposition et planification de tâches' },
  { name: 'architecte', role: 'architecte', desc: 'Conception architecture logicielle' },
];

function AgentLauncherPanel({ app }: { app: AppApi }) {
  const [selected, setSelected] = useState('codeur');
  const [request, setRequest] = useState('');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [providerRef, setProviderRef] = useState('');
  const [modelRef, setModelRef] = useState('');

  const handleLaunch = async () => {
    setLoading(true);
    setResult(null);
    try {
      const body = { target: selected, request };
      if (providerRef) body.provider_ref = providerRef;
      if (modelRef) body.model_ref = modelRef;
      const res = await invoke<any>('daemon_post', { route: 'agent/launch', body: JSON.stringify(body) });
      setResult(res);
    } catch (e: any) {
      setResult({ error: e.message });
    }
    setLoading(false);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', height: '100%' }}>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {SOLO_AGENTS.map(a => (
          <button key={a.name}
            onClick={() => setSelected(a.name)}
            style={{
              padding: '0.2rem 0.5rem',
              fontSize: '0.65rem',
              borderRadius: '0.3rem',
              border: '1px solid',
              borderColor: selected === a.name ? '#3b82f6' : '#334155',
              backgroundColor: selected === a.name ? '#1e3a5f' : '#0f172a',
              color: selected === a.name ? '#93c5fd' : '#94a3b8',
              cursor: 'pointer',
            }}>
            {a.name}
          </button>
        ))}
      </div>

      <div style={{ fontSize: '0.7rem', color: '#64748b' }}>
        {SOLO_AGENTS.find(a => a.name === selected)?.desc}
      </div>

      <textarea
        value={request}
        onChange={e => setRequest(e.target.value)}
        placeholder="Description de la tâche pour cet agent..."
        style={{
          flex: 1,
          minHeight: '60px',
          backgroundColor: '#1e293b',
          color: '#e2e8f0',
          border: '1px solid #334155',
          borderRadius: '0.3rem',
          padding: '0.4rem',
          fontSize: '0.72rem',
          fontFamily: 'monospace',
          resize: 'vertical',
        }}
      />

      <div style={{ display: 'flex', gap: '0.3rem', fontSize: '0.7rem' }}>
        <input
          value={providerRef}
          onChange={e => setProviderRef(e.target.value)}
          placeholder="provider (opt)"
          style={{
            flex: 1,
            backgroundColor: '#1e293b',
            color: '#e2e8f0',
            border: '1px solid #334155',
            borderRadius: '0.3rem',
            padding: '0.2rem 0.4rem',
            fontSize: '0.68rem',
          }}
        />
        <input
          value={modelRef}
          onChange={e => setModelRef(e.target.value)}
          placeholder="modèle (opt)"
          style={{
            flex: 1,
            backgroundColor: '#1e293b',
            color: '#e2e8f0',
            border: '1px solid #334155',
            borderRadius: '0.3rem',
            padding: '0.2rem 0.4rem',
            fontSize: '0.68rem',
          }}
        />
      </div>

      <button
        onClick={handleLaunch}
        disabled={loading || !request.trim()}
        style={{
          padding: '0.3rem 0.8rem',
          backgroundColor: loading ? '#334155' : '#1d4ed8',
          color: '#e2e8f0',
          border: 'none',
          borderRadius: '0.3rem',
          cursor: loading || !request.trim() ? 'not-allowed' : 'pointer',
          fontSize: '0.72rem',
          fontWeight: '600',
        }}>
        {loading ? '⏳ Lancement…' : '▶ Lancer l\'agent'}
      </button>

      {result && (
        <div style={{
          flex: 1,
          overflow: 'auto',
          backgroundColor: '#1e293b',
          border: '1px solid #334155',
          borderRadius: '0.3rem',
          padding: '0.4rem',
          fontSize: '0.7rem',
          fontFamily: 'monospace',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}>
          {result.ok !== false
            ? JSON.stringify(result.result || result, null, 2).substring(0, 4000)
            : `❌ Erreur : ${result.error || 'inconnue'}`}
        </div>
      )}
    </div>
  );
}

export default AgentLauncherPanel;
