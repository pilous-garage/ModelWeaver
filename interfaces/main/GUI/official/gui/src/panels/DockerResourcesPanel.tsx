import React, { useEffect, useState } from 'react';
import { daemonPost } from '../bridge.ts';

interface CacheInfo { name: string; image: string; local: boolean }
interface ContainerInfo { [name: string]: any }

export function DockerResourcesPanel({ app }: { app: any }) {
  const [caches, setCaches] = useState<CacheInfo[]>([]);
  const [containers, setContainers] = useState<ContainerInfo>({});
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [cacheName, setCacheName] = useState('');
  const [cacheBase, setCacheBase] = useState('python:3.12-slim');
  const [cacheTools, setCacheTools] = useState('pytest,git');
  const [agentId, setAgentId] = useState('');
  const [projectId, setProjectId] = useState('');
  const [container, setContainer] = useState('');
  const [testCmd, setTestCmd] = useState('python3 -m pytest -q');
  const [output, setOutput] = useState('');
  const [busy, setBusy] = useState('');

  const refresh = async () => {
    try {
      const r = await daemonPost('docker/caches/list', {});
      setCaches(r?.result?.caches || r?.caches || []);
      const s = await daemonPost('docker/status', {});
      setContainers(s?.result?.containers || s?.containers || {});
      setErr('');
    } catch (e: any) {
      setErr(e.message || 'erreur');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const run = async (route: string, body: any, busyMsg: string) => {
    setBusy(busyMsg);
    setOutput('');
    setErr('');
    try {
      const r = await daemonPost(route, body);
      setOutput(JSON.stringify(r, null, 2));
      await refresh();
    } catch (e: any) {
      setErr(e.message || 'erreur');
    } finally {
      setBusy('');
    }
  };

  const input = (v: string, set: (s: string) => void) => ({
    value: v,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => set(e.target.value),
    style: inp,
  });

  return (
    <div style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem', overflow: 'auto', height: '100%' }}>
      <h3 style={{ fontSize: '0.95rem', fontWeight: '600', margin: 0 }}>🐳 Ressources Docker (swarm)</h3>

      {err && <div style={{ color: '#fca5a5', fontSize: '0.75rem' }}>{err}</div>}
      {busy && <div style={{ color: '#fbbf24', fontSize: '0.75rem' }}>{busy}…</div>}
      {output && (
        <pre style={{ backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.375rem', padding: '0.6rem', fontSize: '0.7rem', overflow: 'auto', maxHeight: '160px', whiteSpace: 'pre-wrap' }}>{output}</pre>
      )}

      {/* Caches */}
      <div style={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '0.5rem', padding: '0.75rem' }}>
        <div style={{ fontSize: '0.78rem', fontWeight: '600', marginBottom: '0.5rem' }}>📦 Caches Docker</div>
        {loading ? <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Chargement…</div> :
          caches.length === 0 ? <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Aucun cache</div> :
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            {caches.map(c => (
              <div key={c.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', alignItems: 'center' }}>
                <span style={{ color: '#e2e8f0' }}>{c.name}</span>
                <span style={{ color: '#94a3b8', fontFamily: 'monospace' }}>{c.image}</span>
                <span style={{ color: c.local ? '#6ee7b7' : '#fca5a5' }}>{c.local ? 'local' : 'absente'}</span>
              </div>
            ))}
          </div>}
        <div style={{ display: 'flex', gap: '0.3rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
          <input placeholder="nom cache" {...input(cacheName, setCacheName)} style={{ ...inp, flex: '1 1 120px' }} />
          <input placeholder="image base" {...input(cacheBase, setCacheBase)} style={{ ...inp, flex: '1 1 140px' }} />
          <input placeholder="outils (virgules)" {...input(cacheTools, setCacheTools)} style={{ ...inp, flex: '1 1 160px' }} />
          <button onClick={() => run('docker/cache/create', { name: cacheName, base: cacheBase, tools: cacheTools.split(',').map(s => s.trim()).filter(Boolean) }, 'Création du cache')} style={btn}>Créer cache</button>
        </div>
      </div>

      {/* Fork / association */}
      <div style={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '0.5rem', padding: '0.75rem' }}>
        <div style={{ fontSize: '0.78rem', fontWeight: '600', marginBottom: '0.5rem' }}>🔗 Fork / association testeur</div>
        <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
          <input placeholder="cache" {...input(container, setContainer)} style={{ ...inp, flex: '1 1 100px' }} />
          <input placeholder="agent_id (testeur)" {...input(agentId, setAgentId)} style={{ ...inp, flex: '1 1 140px' }} />
          <input placeholder="project_id" {...input(projectId, setProjectId)} style={{ ...inp, flex: '1 1 100px' }} />
          <button onClick={() => run('docker/fork', { cache: container || caches[0]?.name, agent_id: agentId, project_id: projectId }, 'Fork')} style={btn}>Fork</button>
        </div>
      </div>

      {/* Batteries de tests */}
      <div style={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '0.5rem', padding: '0.75rem' }}>
        <div style={{ fontSize: '0.78rem', fontWeight: '600', marginBottom: '0.5rem' }}>🧪 Batteries de tests (conteneur persistant)</div>
        <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
          <select value={container} onChange={e => setContainer(e.target.value)} style={inp}>
            <option value="">-- conteneur --</option>
            {Object.keys(containers).map(c => <option key={c} value={c}>{c} ({containers[c]?.status})</option>)}
          </select>
          <input placeholder="commande de test" {...input(testCmd, setTestCmd)} style={{ ...inp, flex: '2 1 200px' }} />
          <button onClick={() => run('docker/run-tests', { container, command: testCmd }, 'Exécution des tests')} style={btn}>Lancer</button>
          <button onClick={() => run('docker/snapshot', { container }, 'Snapshot')} style={btn}>Snapshot</button>
          <button onClick={() => run('docker/release', { container }, 'Release')} style={{ ...btn, backgroundColor: '#7f1d1d', borderColor: '#b91c1c' }}>Libérer</button>
        </div>
      </div>

      {/* Conteneurs */}
      <div style={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '0.5rem', padding: '0.75rem' }}>
        <div style={{ fontSize: '0.78rem', fontWeight: '600', marginBottom: '0.5rem' }}>🖥️ Conteneurs gérés ({Object.keys(containers).length})</div>
        {Object.keys(containers).length === 0 ? <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Aucun conteneur</div> :
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            {Object.entries(containers).map(([name, meta]) => (
              <div key={name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', gap: '0.5rem', alignItems: 'center' }}>
                <span style={{ color: '#e2e8f0', fontFamily: 'monospace' }}>{name}</span>
                <span style={{ color: '#94a3b8' }}>testeur: {meta?.agent_id || '—'} · cache: {meta?.cache || '—'} · projet: {meta?.project_id || '—'}</span>
                <span style={{ color: meta?.status === 'running' ? '#6ee7b7' : '#fca5a5' }}>{meta?.status}</span>
              </div>
            ))}
          </div>}
      </div>
    </div>
  );
}

const inp: React.CSSProperties = {
  backgroundColor: '#0f172a', color: '#e2e8f0', border: '1px solid #475569',
  borderRadius: '0.25rem', fontSize: '0.7rem', padding: '0.3rem', outline: 'none',
};

const btn: React.CSSProperties = {
  padding: '0.3rem 0.6rem', fontSize: '0.7rem', backgroundColor: '#1d4ed8',
  color: '#e2e8f0', border: '1px solid #3b82f6', borderRadius: '0.25rem',
  cursor: 'pointer',
};
