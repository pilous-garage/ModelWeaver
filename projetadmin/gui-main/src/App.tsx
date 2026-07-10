import React, { useState, useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';

const log = async (level: string, msg: string) => {
  try { await invoke('log_message', { level, message: msg }); } catch {}
};

type Mode = 'CHECKING' | 'INSTALLING' | 'DASHBOARD' | 'ERROR';
interface DepStatus {
  name: string; installed: boolean; version: string | null; min_version: string | null;
}
interface SysInfo {
  os: string; arch: string; home: string;
}
interface PythonDep {
  name: string; module: string; installed: boolean; version: string | null; min_version: string | null;
}
interface InstallJob {
  id: number; name: string; job_type: string; status: string; log: string;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      backgroundColor: '#1e293b', borderRadius: '0.5rem',
      border: '1px solid #334155', marginBottom: '1.5rem', overflow: 'hidden'
    }}>
      <div style={{
        padding: '0.75rem 1rem', borderBottom: '1px solid #334155',
        fontWeight: '600', fontSize: '0.875rem', color: '#94a3b8'
      }}>
        {title}
      </div>
      <div style={{ padding: '0.75rem 1rem', fontSize: '0.875rem' }}>
        {children}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <div style={{
      width: '32px', height: '32px', border: '3px solid #3b82f6',
      borderTop: '3px solid transparent', borderRadius: '50%',
      animation: 'spin 1s linear infinite', margin: '0 auto 1rem'
    }} />
  );
}

function App() {
  const [mode, setMode] = useState<Mode>('CHECKING');
  const [deps, setDeps] = useState<DepStatus[]>([]);
  const [sysInfo, setSysInfo] = useState<SysInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);

  const [dbStatus, setDbStatus] = useState<any>(null);
  const [dbLoading, setDbLoading] = useState(false);
  const [dbIniting, setDbIniting] = useState(false);

  const [pythonDeps, setPythonDeps] = useState<PythonDep[] | null>(null);
  const [depsLoading, setDepsLoading] = useState(false);

  const [queue, setQueue] = useState<InstallJob[]>([]);
  const [selectedPip, setSelectedPip] = useState<Set<string>>(new Set());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const check = async () => {
    await log('INFO', 'check() started');
    try {
      setMode('CHECKING');
      const [result, info]: [DepStatus[], SysInfo] = await Promise.all([
        invoke('check_dependencies'),
        invoke('get_system_info'),
      ]);
      setDeps(result);
      setSysInfo(info);
      const missing = result.filter(d => !d.installed);
      const next = missing.length === 0 ? 'DASHBOARD' : 'INSTALLING';
      await log('INFO', `check() done: ${result.length} deps, ${missing.length} missing, mode=${next}`);
      setMode(next);
    } catch (e) {
      await log('ERROR', `check() failed: ${e}`);
      setError(`${e}`);
      setMode('ERROR');
    }
  };

  useEffect(() => { check(); }, []);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    const hasActive = queue.some(j => j.status === 'queued' || j.status === 'running');
    if (hasActive) {
      pollRef.current = setInterval(async () => {
        try {
          const q: InstallJob[] = await invoke('install_queue_status');
          setQueue(q);
          const stillActive = q.some(j => j.status === 'queued' || j.status === 'running');
          if (!stillActive && pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        } catch { }
      }, 1000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [queue]);

  const installDep = async (name: string) => {
    await log('INSTALL', `installDep(${name}) clicked`);
    setInstalling(name);
    setLog(l => [...l, `Installation de ${name}...`]);
    try {
      const result: string = await invoke('install_dependency', { name });
      await log('INSTALL', `installDep(${name}) OK: ${result}`);
      setLog(l => [...l, `✓ ${result}`]);
      await check();
    } catch (e) {
      await log('INSTALL', `installDep(${name}) FAILED: ${e}`);
      setLog(l => [...l, `✗ ${name}: ${e}`]);
      setError(`Échec installation ${name}: ${e}`);
    } finally {
      setInstalling(null);
    }
  };

  const handleCheckDb = async () => {
    await log('DB', 'handleCheckDb');
    setDbLoading(true);
    try {
      const r: any = await invoke('check_databases');
      await log('DB', `check_databases result: ${JSON.stringify(r)}`);
      setDbStatus(r);
    } catch (e) {
      await log('DB', `check_databases error: ${e}`);
      setLog(l => [...l, `✗ check_db: ${e}`]);
    } finally {
      setDbLoading(false);
    }
  };

  const handleInitDb = async () => {
    await log('DB', 'handleInitDb');
    setDbIniting(true);
    setLog(l => [...l, `Initialisation des bases...`]);
    try {
      const r: any = await invoke('init_databases');
      await log('DB', `init_databases OK: ${r.mw_db}`);
      setDbStatus(null);
      setLog(l => [...l, `✓ Bases créées: ${r.mw_db}`]);
      await handleCheckDb();
    } catch (e) {
      await log('DB', `init_databases FAILED: ${e}`);
      setLog(l => [...l, `✗ init_db: ${e}`]);
    } finally {
      setDbIniting(false);
    }
  };

  const handleCheckPipDeps = async () => {
    await log('PIP', 'handleCheckPipDeps');
    setDepsLoading(true);
    try {
      const r: any = await invoke('check_python_deps');
      await log('PIP', `check_python_deps: ${r.deps?.length} deps`);
      setPythonDeps(r.deps as PythonDep[]);
      setSelectedPip(new Set(
        (r.deps as PythonDep[]).filter((d: PythonDep) => !d.installed).map(d => d.name)
      ));
    } catch (e) {
      await log('PIP', `check_python_deps error: ${e}`);
      setLog(l => [...l, `✗ check_pip: ${e}`]);
    } finally {
      setDepsLoading(false);
    }
  };

  const handleInstallPip = async (name: string) => {
    await log('PIP', `handleInstallPip(${name})`);
    try {
      setQueue(await invoke('install_queue_status'));
      await invoke('install_queue_add', { name, jobType: 'pip' });
      const q: InstallJob[] = await invoke('install_queue_status');
      setQueue(q);
    } catch (e) {
      await log('PIP', `handleInstallPip error: ${e}`);
      setLog(l => [...l, `✗ queue_add ${name}: ${e}`]);
    }
  };

  const handleInstallAllPip = async () => {
    await log('PIP', `handleInstallAllPip (${selectedPip.size} items)`);
    for (const name of selectedPip) {
      try {
        await invoke('install_queue_add', { name, jobType: 'pip' });
      } catch (e) {
        await log('PIP', `queue_add ${name} error: ${e}`);
        setLog(l => [...l, `✗ queue_add ${name}: ${e}`]);
      }
    }
    const q: InstallJob[] = await invoke('install_queue_status');
    setQueue(q);
  };

  const togglePipDep = (name: string) => {
    const next = new Set(selectedPip);
    if (next.has(name)) next.delete(name); else next.add(name);
    setSelectedPip(next);
  };

  return (
    <div style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      backgroundColor: '#0f172a', color: '#e2e8f0',
      fontFamily: 'Inter, system-ui, sans-serif', padding: '2rem'
    }}>
      <div style={{ maxWidth: '700px', width: '100%', margin: '0 auto' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 'bold', marginBottom: '1.5rem' }}>
          ModelWeaver
        </h1>

        {/* CHECKING */}
        {mode === 'CHECKING' && (
          <div style={{ textAlign: 'center', padding: '3rem' }}>
            <Spinner />
            <p style={{ color: '#94a3b8' }}>Vérification de l'environnement...</p>
          </div>
        )}

        {/* INSTALLING (system deps) */}
        {mode === 'INSTALLING' && (
          <div>
            <p style={{ color: '#f59e0b', marginBottom: '1rem' }}>
              Dépendances système manquantes :
            </p>
            {deps.filter(d => !d.installed).map(d => (
              <div key={d.name} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '0.75rem 1rem', backgroundColor: '#1e293b',
                borderRadius: '0.5rem', marginBottom: '0.5rem',
                border: '1px solid #334155'
              }}>
                <span style={{ fontWeight: '500' }}>{d.name}</span>
                <span style={{ fontSize: '0.875rem', color: '#64748b' }}>
                  min: {d.min_version}
                </span>
                <button onClick={() => installDep(d.name)}
                  disabled={installing === d.name}
                  style={{
                    padding: '0.375rem 1rem', backgroundColor: '#3b82f6',
                    color: 'white', border: 'none', borderRadius: '0.375rem',
                    cursor: 'pointer', fontSize: '0.8125rem',
                    opacity: installing === d.name ? '0.5' : '1'
                  }}>
                  {installing === d.name ? 'Installation...' : 'Installer'}
                </button>
              </div>
            ))}
            {log.length > 0 && (
              <div style={{
                backgroundColor: '#0f172a', borderRadius: '0.5rem',
                border: '1px solid #334155', padding: '0.75rem 1rem',
                maxHeight: '150px', overflowY: 'auto', marginTop: '1rem',
                fontSize: '0.75rem', fontFamily: 'monospace'
              }}>
                {log.map((l, i) => <div key={i} style={{ color: l.startsWith('✗') ? '#fca5a5' : l.startsWith('✓') ? '#6ee7b7' : '#94a3b8' }}>{l}</div>)}
              </div>
            )}
            <button onClick={check} style={{
              marginTop: '1rem', padding: '0.5rem 1.5rem',
              backgroundColor: '#1e293b', color: '#94a3b8',
              border: '1px solid #334155', borderRadius: '0.375rem'
            }}>Re-vérifier</button>
          </div>
        )}

        {/* DASHBOARD */}
        {mode === 'DASHBOARD' && (
          <div>
            {/* Système */}
            <Card title="SYSTÈME">
              {sysInfo && (
                <>
                  <Row label="OS" value={`${sysInfo.os} (${sysInfo.arch})`} />
                  <Row label="Home" value={sysInfo.home} />
                </>
              )}
            </Card>

            {/* Dépendances système */}
            <Card title="DÉPENDANCES SYSTÈME">
              {deps.map(d => (
                <Row key={d.name} label={d.name}
                  value={d.installed ? d.version || '✓' : '✗ manquant'}
                  color={d.installed ? '#6ee7b7' : '#fca5a5'} />
              ))}
            </Card>

            {/* Bases de données */}
            <Card title="BASES DE DONNÉES">
              {!dbStatus && (
                <button onClick={handleCheckDb} disabled={dbLoading}
                  style={btnStyle}>
                  {dbLoading ? 'Vérification...' : 'Vérifier les bases'}
                </button>
              )}
              {dbStatus && (
                <>
                  <Row label="modelweaver.db"
                    value={dbStatus.modelweaver_db?.exists ? '✓ existe' : '✗ manquant'}
                    color={dbStatus.modelweaver_db?.exists ? '#6ee7b7' : '#fca5a5'} />
                  {dbStatus.modelweaver_db?.tool_count !== undefined && (
                    <Row label="  → outils"
                      value={`${dbStatus.modelweaver_db.tool_count} définitions`} />
                  )}
                  <Row label="catalogue.db"
                    value={dbStatus.catalogue_db?.exists ? '✓ existe' : '✗ manquant'}
                    color={dbStatus.catalogue_db?.exists ? '#6ee7b7' : '#fca5a5'} />
                  {dbStatus.catalogue_db?.provider_count !== undefined && (
                    <Row label="  → fournisseurs"
                      value={`${dbStatus.catalogue_db.provider_count} enregistrés`} />
                  )}
                  {(!dbStatus.modelweaver_db?.exists || !dbStatus.catalogue_db?.exists) && (
                    <button onClick={handleInitDb} disabled={dbIniting}
                      style={{ ...btnStyle, marginTop: '0.75rem' }}>
                      {dbIniting ? 'Initialisation...' : 'Initialiser les bases'}
                    </button>
                  )}
                </>
              )}
              {dbStatus && dbStatus.modelweaver_db?.exists && dbStatus.catalogue_db?.exists && (
                <span style={{ color: '#6ee7b7', fontSize: '0.8125rem' }}>
                  ✓ Bases prêtes
                </span>
              )}
            </Card>

            {/* Dépendances Python */}
            <Card title="DÉPENDANCES PYTHON">
              {!pythonDeps && (
                <button onClick={handleCheckPipDeps} disabled={depsLoading}
                  style={btnStyle}>
                  {depsLoading ? 'Analyse...' : 'Analyser les dépendances pip'}
                </button>
              )}
              {pythonDeps && (
                <>
                  {pythonDeps.map(d => (
                    <div key={d.name} style={{
                      display: 'flex', alignItems: 'center', gap: '0.5rem',
                      padding: '0.25rem 0'
                    }}>
                      <input type="checkbox" checked={selectedPip.has(d.name)}
                        onChange={() => togglePipDep(d.name)}
                        style={{ accentColor: '#3b82f6' }} />
                      <span style={{ flex: 1 }}>{d.name}</span>
                      <span style={{
                        color: d.installed ? '#6ee7b7' : '#fca5a5',
                        fontSize: '0.75rem'
                      }}>
                        {d.installed ? (d.version || '✓') : '✗'}
                      </span>
                    </div>
                  ))}
                  {selectedPip.size > 0 && (
                    <button onClick={handleInstallAllPip}
                      style={{ ...btnStyle, marginTop: '0.75rem' }}>
                      Installer la sélection ({selectedPip.size})
                    </button>
                  )}
                </>
              )}
            </Card>

            {/* File d'installation */}
            {queue.length > 0 && (
              <Card title="FILE D'INSTALLATION">
                {queue.map(j => (
                  <div key={j.id} style={{
                    display: 'flex', alignItems: 'center', gap: '0.75rem',
                    padding: '0.5rem 0', borderBottom: '1px solid #1e293b',
                    fontSize: '0.8125rem'
                  }}>
                    <span style={{
                      width: '8px', height: '8px', borderRadius: '50%',
                      backgroundColor: j.status === 'completed' ? '#6ee7b7'
                        : j.status === 'failed' ? '#fca5a5'
                        : j.status === 'running' ? '#3b82f6' : '#64748b'
                    }} />
                    <span style={{ flex: 1 }}>{j.name}</span>
                    <span style={{ color: '#64748b' }}>{j.status}</span>
                    {j.log && j.log.length > 100 && (
                      <span style={{ fontSize: '0.65rem', color: '#64748b', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {j.log.slice(0, 100)}
                      </span>
                    )}
                  </div>
                ))}
              </Card>
            )}

            {/* Log */}
            {log.length > 0 && (
              <div style={{
                backgroundColor: '#0f172a', borderRadius: '0.5rem',
                border: '1px solid #334155', padding: '0.75rem 1rem',
                maxHeight: '150px', overflowY: 'auto', marginBottom: '1.5rem',
                fontSize: '0.75rem', fontFamily: 'monospace'
              }}>
                {log.map((l, i) => (
                  <div key={i} style={{ color: l.startsWith('✗') ? '#fca5a5' : l.startsWith('✓') ? '#6ee7b7' : '#94a3b8' }}>
                    {l}
                  </div>
                ))}
              </div>
            )}

            <button onClick={check} style={{
              padding: '0.5rem 1.5rem', backgroundColor: '#3b82f6',
              color: 'white', border: 'none', borderRadius: '0.375rem',
              fontSize: '0.8125rem', cursor: 'pointer'
            }}>
              Re-vérifier les dépendances
            </button>
          </div>
        )}

        {/* ERROR */}
        {mode === 'ERROR' && (
          <div style={{
            padding: '1rem', backgroundColor: '#450a0a',
            borderRadius: '0.5rem', color: '#fca5a5'
          }}>
            <p style={{ fontWeight: 'bold', marginBottom: '0.5rem' }}>Erreur</p>
            <p style={{ fontSize: '0.875rem' }}>{error}</p>
            {log.length > 0 && (
              <div style={{
                backgroundColor: '#0f172a', borderRadius: '0.5rem',
                border: '1px solid #334155', padding: '0.75rem 1rem',
                maxHeight: '150px', overflowY: 'auto', marginTop: '1rem',
                fontSize: '0.75rem', fontFamily: 'monospace'
              }}>
                {log.map((l, i) => (
                  <div key={i} style={{ color: l.startsWith('✗') ? '#fca5a5' : l.startsWith('✓') ? '#6ee7b7' : '#94a3b8' }}>{l}</div>
                ))}
              </div>
            )}
            <button onClick={() => { setError(null); check(); }}
              style={{
                marginTop: '0.75rem', padding: '0.5rem 1rem',
                backgroundColor: '#7f1d1d', color: '#fca5a5',
                border: '1px solid #991b1b', borderRadius: '0.375rem',
                cursor: 'pointer'
              }}>
              Réessayer
            </button>
          </div>
        )}
      </div>
      <style>{`@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
      <span style={{ color: '#64748b' }}>{label}</span>
      <span style={{ color: color || '#e2e8f0' }}>{value}</span>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  padding: '0.375rem 1rem', backgroundColor: '#3b82f6',
  color: 'white', border: 'none', borderRadius: '0.375rem',
  cursor: 'pointer', fontSize: '0.8125rem'
};

export default App;
