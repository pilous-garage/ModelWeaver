import React, { useState, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/core';

type Step = 'INIT' | 'CHECK_UPDATE' | 'UPDATE_PROMPT' | 'DOWNLOAD' | 'UNPACK' | 'LAUNCH' | 'DONE' | 'ERROR';

interface LogEntry {
  msg: string;
  type: 'info' | 'success' | 'error';
}

function App() {
  const [step, setStep] = useState<Step>('INIT');
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [platform, setPlatform] = useState<string>('');
  const [latestVersion, setLatestVersion] = useState<string>('');

  const addLog = (msg: string, type: LogEntry['type'] = 'info') => {
    setLogs(prev => [...prev, { msg, type }]);
  };

  useEffect(() => {
    if (step !== 'INIT') return;
    run();
  }, [step]);

  const resumeDownload = async () => {
    try {
      const info: any = { os: platform.split('/')[0], arch: platform.split('/')[1] };
      addLog('Téléchargement du release ModelWeaver...', 'info');
      setStep('DOWNLOAD');
      const archive = await invoke('download_release', {
        url: `https://github.com/pilous-garage/ModelWeaver/releases/latest/download/modelweaver-main-${info.os}-${info.arch}.tar.gz`
      });
      addLog(`Téléchargé : ${archive}`, 'success');

      setStep('UNPACK');
      addLog("Extraction de l'archive...", 'info');
      const dest = await invoke('unpack_release', { archivePath: archive });
      addLog(`Dépaqueté dans : ${dest}`, 'success');

      setStep('LAUNCH');
      addLog('Lancement de ModelWeaver...', 'info');
      await invoke('launch_main');
      addLog('ModelWeaver est lancé !', 'success');
      setStep('DONE');
    } catch (e) {
      addLog(`Erreur : ${e}`, 'error');
      setError(`${e}`);
      setStep('ERROR');
    }
  };

  const cancelDownload = () => {
    addLog('Mise à jour annulée par l\'utilisateur', 'error');
    setError('Mise à jour annulée');
    setStep('ERROR');
  };

  const run = async () => {
    try {
      // Platform
      const info: any = await invoke('get_platform');
      setPlatform(`${info.os}/${info.arch}`);
      addLog(`Plateforme détectée : ${info.os} (${info.arch})`, 'info');

      // Check update
      setStep('CHECK_UPDATE');
      addLog('Vérification de la version du bootstrap...', 'info');
      const latest = await invoke('check_update');
      const tag = latest.split('-')[0];
      setLatestVersion(tag);
      addLog(`Dernière version disponible : ${tag}`, 'success');
      setStep('UPDATE_PROMPT');
    } catch (e) {
      addLog(`Erreur : ${e}`, 'error');
      setError(`${e}`);
      setStep('ERROR');
    }
  };

  return (
    <div style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      backgroundColor: '#0f172a', color: '#e2e8f0',
      fontFamily: 'ui-monospace, monospace', padding: '1.5rem'
    }}>
      <div style={{ marginBottom: '1rem' }}>
        <h1 style={{ fontSize: '1.25rem', fontWeight: 'bold', margin: 0 }}>
          ModelWeaver Bootstrap
        </h1>
        {platform && (
          <span style={{ fontSize: '0.75rem', color: '#64748b' }}>{platform}</span>
        )}
      </div>

      <div style={{
        flex: 1, backgroundColor: '#1e293b', borderRadius: '0.5rem',
        padding: '1rem', overflowY: 'auto', fontSize: '0.8125rem',
        border: '1px solid #334155'
      }}>
        {logs.map((log, i) => (
          <div key={i} style={{
            color: log.type === 'success' ? '#22c55e'
                 : log.type === 'error' ? '#ef4444'
                 : '#94a3b8',
            marginBottom: '0.25rem'
          }}>
            {log.type === 'success' ? '✓ ' : log.type === 'error' ? '✗ ' : '→ '}
            {log.msg}
          </div>
        ))}
        {step === 'UPDATE_PROMPT' && (
          <div style={{
            marginTop: '1rem', padding: '1rem', backgroundColor: '#1e3a5f',
            borderRadius: '0.5rem', textAlign: 'center'
          }}>
            <p style={{ margin: '0 0 1rem', color: '#93c5fd', fontSize: '0.875rem' }}>
              Nouvelle version disponible : {latestVersion}
            </p>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
              <button onClick={resumeDownload} style={{
                padding: '0.5rem 1.5rem', backgroundColor: '#3b82f6',
                color: 'white', border: 'none', borderRadius: '0.375rem',
                cursor: 'pointer', fontSize: '0.8125rem'
              }}>
                Télécharger et installer
              </button>
              <button onClick={cancelDownload} style={{
                padding: '0.5rem 1.5rem', backgroundColor: '#1e293b',
                color: '#94a3b8', border: '1px solid #334155',
                borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem'
              }}>
                Annuler
              </button>
            </div>
          </div>
        )}
        {(step !== 'DONE' && step !== 'ERROR' && step !== 'UPDATE_PROMPT') && (
          <div style={{ color: '#64748b', marginTop: '0.5rem' }}>En cours...</div>
        )}
        {step === 'DONE' && (
          <div style={{
            marginTop: '1rem', padding: '0.5rem', backgroundColor: '#064e3b',
            borderRadius: '0.25rem', color: '#6ee7b7', textAlign: 'center'
          }}>
            ModelWeaver est en cours d'exécution. Vous pouvez fermer cette fenêtre.
          </div>
        )}
        {step === 'ERROR' && (
          <div style={{
            marginTop: '1rem', padding: '0.5rem', backgroundColor: '#450a0a',
            borderRadius: '0.25rem', color: '#fca5a5', textAlign: 'center'
          }}>
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
