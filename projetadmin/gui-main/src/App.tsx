import React, { useState, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/core';

type Mode = 'CHECKING' | 'INSTALLING' | 'DASHBOARD' | 'ERROR';
interface DepStatus {
  name: string; installed: boolean; version: string | null; min_version: string | null;
}

function App() {
  const [mode, setMode] = useState<Mode>('CHECKING');
  const [deps, setDeps] = useState<DepStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);

  const check = async () => {
    try {
      setMode('CHECKING');
      const result: DepStatus[] = await invoke('check_dependencies');
      setDeps(result);
      const missing = result.filter(d => !d.installed);
      if (missing.length === 0) {
        setMode('DASHBOARD');
      } else {
        setMode('INSTALLING');
      }
    } catch (e) {
      setError(`${e}`);
      setMode('ERROR');
    }
  };

  useEffect(() => { check(); }, []);

  const installDep = async (name: string) => {
    setInstalling(name);
    try {
      await invoke('install_dependency', { name });
      await check();
    } catch (e) {
      setError(`Échec installation ${name}: ${e}`);
    } finally {
      setInstalling(null);
    }
  };

  return (
    <div style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      backgroundColor: '#0f172a', color: '#e2e8f0',
      fontFamily: 'Inter, system-ui, sans-serif', padding: '2rem'
    }}>
      <div style={{ maxWidth: '600px', width: '100%', margin: '0 auto' }}>

        <h1 style={{ fontSize: '1.5rem', fontWeight: 'bold', marginBottom: '1.5rem' }}>
          ModelWeaver
        </h1>

        {mode === 'CHECKING' && (
          <div style={{ textAlign: 'center', padding: '3rem' }}>
            <div style={{
              width: '32px', height: '32px', border: '3px solid #3b82f6',
              borderTop: '3px solid transparent', borderRadius: '50%',
              animation: 'spin 1s linear infinite', margin: '0 auto 1rem'
            }}></div>
            <p style={{ color: '#94a3b8' }}>Vérification de l'environnement...</p>
          </div>
        )}

        {mode === 'INSTALLING' && (
          <div>
            <p style={{ color: '#f59e0b', marginBottom: '1rem' }}>
              Certaines dépendances sont manquantes :
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
                <button
                  onClick={() => installDep(d.name)}
                  disabled={installing === d.name}
                  style={{
                    padding: '0.375rem 1rem', backgroundColor: '#3b82f6',
                    color: 'white', border: 'none', borderRadius: '0.375rem',
                    cursor: 'pointer', fontSize: '0.8125rem',
                    opacity: installing === d.name ? '0.5' : '1'
                  }}
                >
                  {installing === d.name ? 'Installation...' : 'Installer'}
                </button>
              </div>
            ))}
            <button onClick={check}
              style={{
                marginTop: '1rem', padding: '0.5rem 1.5rem',
                backgroundColor: '#1e293b', color: '#94a3b8',
                border: '1px solid #334155', borderRadius: '0.375rem'
              }}
            >
              Re-vérifier
            </button>
          </div>
        )}

        {mode === 'DASHBOARD' && (
          <div>
            <div style={{
              padding: '0.75rem 1rem', backgroundColor: '#064e3b',
              borderRadius: '0.5rem', color: '#6ee7b7', marginBottom: '1.5rem',
              textAlign: 'center', fontWeight: '500'
            }}>
              ✓ Toutes les dépendances sont installées
            </div>
            <div style={{
              padding: '2rem',             border: '2px dashed #334155',
              borderRadius: '0.75rem', textAlign: 'center',
              color: '#64748b', fontStyle: 'italic'
            }}>
              [ Dashboard — à construire ]
            </div>
            <button onClick={check}
              style={{
                marginTop: '1rem', padding: '0.5rem 1.5rem',
                backgroundColor: '#1e293b', color: '#94a3b8',
                border: '1px solid #334155', borderRadius: '0.375rem',
                fontSize: '0.8125rem', cursor: 'pointer'
              }}
            >
              Re-vérifier les dépendances
            </button>
          </div>
        )}

        {mode === 'ERROR' && (
          <div style={{
            padding: '1rem', backgroundColor: '#450a0a',
            borderRadius: '0.5rem', color: '#fca5a5'
          }}>
            <p style={{ fontWeight: 'bold', marginBottom: '0.5rem' }}>Erreur</p>
            <p style={{ fontSize: '0.875rem' }}>{error}</p>
            <button onClick={check}
              style={{
                marginTop: '0.75rem', padding: '0.5rem 1rem',
                backgroundColor: '#7f1d1d', color: '#fca5a5',
                border: '1px solid #991b1b', borderRadius: '0.375rem',
                cursor: 'pointer'
              }}
            >
              Réessayer
            </button>
          </div>
        )}

      </div>
      <style>{`
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

export default App;
