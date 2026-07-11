import React, { useState, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/core';

interface Config {
  warning_size_download_mb: number;
  restart_delay_seconds: number;
  dont_close_old_bootstrap: boolean;
}

type Step = 'INIT' | 'CHECK_UPDATE' | 'UPDATE_PROMPT' | 'TOS_ACCEPTANCE' | 'SIZE_CONFIRMATION' | 'DOWNLOAD' | 'UNPACK' | 'LAUNCH' | 'DONE' | 'ERROR' | 'FALLBACK_DOWNLOAD' | 'RESTARTING';
interface LogEntry {
  msg: string;
  type: 'info' | 'success' | 'error';
}

function LogPanel({ log }: { log: LogEntry[] }) {
  return (
    <div style={{
      backgroundColor: '#0f172a', borderRadius: '0.5rem',
      border: '1px solid #334155', padding: '0.75rem 1rem',
      maxHeight: '300px', minHeight: '100px', overflowY: 'auto',
      marginBottom: '1rem', textAlign: 'left',
      fontSize: '0.75rem', fontFamily: 'ui-monospace, monospace'
    }}>
      {log.map((l, i) => (
        <div key={i} style={{
          color: l.type === 'success' ? '#22c55e' : l.type === 'error' ? '#ef4444' : '#94a3b8',
          marginBottom: '0.25rem', whiteSpace: 'pre-wrap', lineHeight: '1.4'
        }}>
          {l.type === 'success' ? '✓ ' : l.type === 'error' ? '✗ ' : '→ '}
          {l.msg}
        </div>
      ))}
    </div>
  );
}

function App() {
  const [step, setStep] = useState<Step>('INIT');
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [platform, setPlatform] = useState<string>('');
  const [currentVersion, setCurrentVersion] = useState<string>('');
  const [latestVersion, setLatestVersion] = useState<string>('');
  const [releaseSize, setReleaseSize] = useState<number>(0);
  const [targetUrl, setTargetUrl] = useState<string>('');
  const [repoUrl, setRepoUrl] = useState<string>('');
  const [fallbackUrl, setFallbackUrl] = useState<string>('');
  const [countdown, setCountdown] = useState<number>(0);
  const [restartDontClose, setRestartDontClose] = useState<boolean>(false);
  const [termsContent, setTermsContent] = useState<string>('');
  const [pendingArchivePath, setPendingArchivePath] = useState<string>('');

  const addLog = (msg: string, type: LogEntry['type'] = 'info') => {
    setLogs(prev => [...prev, { msg, type }]);
  };

  useEffect(() => {
    if (step !== 'INIT') return;
    invoke('log_error', { message: "Frontend: Démarrage de l'application" }).catch(() => {});
    run();
  }, [step]);

  const run = async () => {
    try {
      console.log("Début de run()");
      const info: any = await invoke('get_platform');
      setPlatform(`${info.os}/${info.arch}`);
      addLog(`Plateforme détectée : ${info.os} (${info.arch})`, 'info');

      const repo = await invoke('get_repository_info') as string;
      setRepoUrl(repo);
      setFallbackUrl(`https://github.com/pilous-garage/ModelWeaver/releases/latest/download/modelweaver-bootstrap-${info.os}-${info.arch}`);

      const current = await invoke('get_current_version') as string;
      setCurrentVersion(current);
      addLog(`Version actuelle : ${current}`, 'info');

      setStep('CHECK_UPDATE');
      addLog('Vérification de la version du bootstrap...', 'info');
      try {
        const latestData = await invoke('check_update') as any;
        console.log("Réponse de check_update:", latestData);
        
        const latestTag = latestData.latest_tag;
        const needsUpdate = latestData.needs_update;
        if (!latestTag) {
          addLog('Impossible de récupérer la version latest depuis GitHub', 'error');
          setStep('ERROR');
          setError('Erreur version latest');
          return;
        }
        setLatestVersion(latestTag);
        addLog(`Dernière version disponible : ${latestTag}`, 'info');

        const currentTag = current.split('-')[0];
        if (needsUpdate) {
          setStep('UPDATE_PROMPT');
        } else if (latestTag !== currentTag) {
          addLog(`Version locale (${currentTag}) différente du dépôt (${latestTag}) — fallback`, 'info');
          setStep('FALLBACK_DOWNLOAD');
        } else {
          addLog('Vous utilisez la dernière version. Tentative de lancement du main app...', 'info');
          try {
            await invoke('launch_main');
            addLog('ModelWeaver est lancé !', 'success');
            setStep('DONE');
          } catch (e) {
            addLog('Main app non installé, téléchargement de la release complète...', 'info');
            const assets = latestData.assets || [];
            let url = "";
            for (const asset of assets) {
              const assetName = asset.name || "";
              if (typeof assetName === 'string' && assetName.includes("modelweaver-release") && assetName.includes(info.os) && assetName.includes(info.arch)) {
                url = asset.browser_download_url || "";
                break;
              }
            }
            if (url) {
              downloadReleaseAndSelfUpdate(url);
            } else {
              addLog('URL de téléchargement introuvable dans les assets GitHub.', 'error');
              setError('URL introuvable');
              setStep('ERROR');
            }
          }
        }
      } catch (e) {
        addLog(`Erreur lors de la vérification : ${e}`, 'error');
        setError(`${e}`);
        setStep('ERROR');
      }
    } catch (e) {
      addLog(`Erreur critique : ${e}`, 'error');
      setError(`${e}`);
      setStep('ERROR');
    }
  };


  const resumeDownload = async (url: string) => {
    try {
      addLog('Vérification de la taille du fichier...', 'info');
      const size = await invoke('get_release_size', { url });
      setReleaseSize(size);

      const config: Config = await invoke('load_config');
      if (size > config.warning_size_download_mb * 1024 * 1024) {
        setTargetUrl(url);
        setStep('SIZE_CONFIRMATION');
      } else {
        await performDownload(url);
      }
  } catch (e) {
      const errorMsg = `Erreur : ${e instanceof Error ? e.message : e}`;
      console.error(errorMsg);
      addLog(errorMsg, 'error');
      addLog('Étrange, cette version n\'est pas disponible dans le dépôt lié.', 'error');
      setError(errorMsg);
      setStep('FALLBACK_DOWNLOAD');
      await invoke('log_error', { message: errorMsg }).catch(() => {});
    }
  };

  const performDownload = async (url: string) => {
    try {
      addLog('Téléchargement du release ModelWeaver...', 'info');
      setStep('DOWNLOAD');
      const archive = await invoke('download_release', { url });
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

  const downloadReleaseAndSelfUpdate = async (url: string) => {
    try {
      addLog('Téléchargement de la release complète...', 'info');
      setStep('DOWNLOAD');
      const archive = await invoke('download_release', { url });
      addLog(`Téléchargé : ${archive}`, 'success');

      const terms = await invoke('load_terms') as string;
      setTermsContent(terms);
      setPendingArchivePath(archive);
      setStep('TOS_ACCEPTANCE');
    } catch (e) {
      addLog(`Erreur : ${e}`, 'error');
      setError(`${e}`);
      setStep('ERROR');
    }
  };

  const handleTosAccept = async () => {
    if (!pendingArchivePath) return;
    try {
      setStep('UNPACK');
      addLog("Extraction de l'archive...", 'info');
      const dest = await invoke('unpack_release', { archivePath: pendingArchivePath });
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

  const handleTosDecline = () => {
    addLog('Installation annulée — CGU non acceptées', 'error');
    setError('CGU non acceptées');
    setStep('ERROR');
  };

  const cancelDownload = () => {
    addLog('Mise à jour annulée par l\'utilisateur', 'error');
    setError('Mise à jour annulée');
    setStep('ERROR');
  };

  const handleSizeConfirm = async (ok: boolean, url: string) => {
    if (ok) {
      await performDownload(url);
    } else {
      cancelDownload();
    }
  };

  const handleFallbackDownload = async () => {
    try {
      addLog('Téléchargement de la dernière version du bootstrap...', 'info');
      const result = await invoke('self_update', { dryRun: false }) as string;
      addLog(result, 'success');

      const config: Config = await invoke('load_config');
      const delay = config.restart_delay_seconds || 10;
      const dontClose = config.dont_close_old_bootstrap || false;

      setRestartDontClose(dontClose);
      setCountdown(delay);
      setStep('RESTARTING');
    } catch (e) {
      addLog(`Erreur lors de la mise à jour : ${e}`, 'error');
      setError(`${e}`);
      setStep('ERROR');
    }
  };

  useEffect(() => {
    if (step !== 'RESTARTING') return;
    if (countdown <= 0) return;

    const timer = setTimeout(() => {
      setCountdown(prev => prev - 1);
    }, 1000);

    return () => clearTimeout(timer);
  }, [step, countdown]);

  useEffect(() => {
    if (step !== 'RESTARTING' || countdown > 0) return;

    addLog('Lancement du nouveau bootstrap...', 'info');
    invoke('restart_app', { dontClose: restartDontClose })
      .then(() => {
        if (restartDontClose) {
          addLog('Nouveau bootstrap lancé. Vous pouvez fermer cette fenêtre.', 'success');
          setStep('DONE');
        }
      })
      .catch(e => {
        addLog(`Erreur lors du redémarrage : ${e}`, 'error');
        setError(`${e}`);
        setStep('ERROR');
      });
  }, [step, countdown, restartDontClose]);

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', backgroundColor: '#0f172a', color: '#e2e8f0', fontFamily: 'ui-monospace, monospace', padding: '1.5rem' }}>
      <div style={{ marginBottom: '1rem' }}>
        <h1 style={{ fontSize: '1.25rem', fontWeight: 'bold', margin: 0 }}>ModelWeaver Bootstrap</h1>
        {platform && <span style={{ fontSize: '0.75rem', color: '#64748b' }}>{platform}</span>}
      </div>

      <div style={{ flex: 1, backgroundColor: '#1e293b', borderRadius: '0.5rem', padding: '1rem', overflowY: 'auto', fontSize: '0.8125rem', border: '1px solid #334155' }}>
        <LogPanel log={logs} />

        {step === 'UPDATE_PROMPT' && (
          <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#1e3a5f', borderRadius: '0.5rem', textAlign: 'center' }}>
            <p style={{ margin: '0 0 1rem', color: '#93c5fd', fontSize: '0.875rem' }}>Nouvelle version disponible : {latestVersion}</p>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
              <button onClick={async () => {
                try {
                  addLog('Mise à jour du bootstrap...', 'info');
                  const result = await invoke('self_update', { dryRun: false }) as string;
                  addLog(result, 'success');
                  addLog('Redémarrage du bootstrap...', 'info');
                  const config: Config = await invoke('load_config');
                  const dontClose = config.dont_close_old_bootstrap || false;
                  await invoke('restart_app', { dontClose });
                } catch (e) {
                  addLog(`Erreur : ${e}`, 'error');
                }
              }} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Télécharger et installer</button>
              <button onClick={cancelDownload} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Annuler</button>
            </div>
          </div>
        )}

        {step === 'TOS_ACCEPTANCE' && (
          <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#1e3a5f', borderRadius: '0.5rem', textAlign: 'center' }}>
            <p style={{ margin: '0 0 0.75rem', color: '#93c5fd', fontSize: '0.875rem' }}>
              Conditions Générales d'Utilisation
            </p>
            <div style={{
              backgroundColor: '#0f172a', borderRadius: '0.375rem',
              border: '1px solid #334155', padding: '0.75rem',
              maxHeight: '250px', overflowY: 'auto',
              marginBottom: '1rem', textAlign: 'left',
              fontSize: '0.75rem', lineHeight: '1.5', whiteSpace: 'pre-wrap'
            }}>
              {termsContent}
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
              <button onClick={handleTosAccept} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Accepter et installer</button>
              <button onClick={handleTosDecline} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Refuser</button>
            </div>
          </div>
        )}

        {step === 'SIZE_CONFIRMATION' && (
          <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#1e3a5f', borderRadius: '0.5rem', textAlign: 'center' }}>
            <p style={{ margin: '0 0 1rem', color: '#fca5a5', fontSize: '0.875rem' }}>L'archive fait {(releaseSize / (1024 * 1024)).toFixed(2)} Mo.</p>
            <p style={{ margin: '0 0 1rem', fontSize: '0.875rem' }}>Voulez-vous quand même continuer le téléchargement ?</p>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
              <button onClick={() => handleSizeConfirm(true, targetUrl)} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Oui, continuer</button>
              <button onClick={() => handleSizeConfirm(false, '')} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Annuler</button>
            </div>
          </div>
        )}

        {step === 'FALLBACK_DOWNLOAD' && (
          <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#1e3a5f', borderRadius: '0.5rem', textAlign: 'center' }}>
            <p style={{ margin: '0 0 0.5rem', color: '#fca5a5', fontSize: '0.875rem' }}>
              Étrange, cette version ({currentVersion}) n'est pas dans le dépôt lié.
            </p>
            <p style={{ margin: '0 0 1rem', fontSize: '0.8125rem', color: '#93c5fd' }}>
              Voulez-vous télécharger le bootstrap {latestVersion} depuis le dépôt officiel ? Il remplacera automatiquement la version actuelle.
            </p>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
              <button onClick={handleFallbackDownload} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Oui, télécharger la dernière version</button>
              <button onClick={() => {
                invoke('open_url', { url: repoUrl }).catch(() => {});
                cancelDownload();
              }} style={{ padding: '0.5rem 1.5rem', backgroundColor: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.8125rem' }}>Aller sur le dépôt</button>
            </div>
          </div>
        )}

        {step === 'RESTARTING' && (
          <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#064e3b', borderRadius: '0.5rem', textAlign: 'center' }}>
            <p style={{ margin: '0 0 0.5rem', color: '#6ee7b7', fontSize: '0.875rem' }}>
              ✓ Mise à jour installée avec succès
            </p>
            <p style={{ margin: '0', fontSize: '0.8125rem', color: '#fca5a5' }}>
              Redémarrage dans {countdown} seconde{countdown > 1 ? 's' : ''}...
            </p>
            {restartDontClose && countdown <= 0 && (
              <p style={{ margin: '0.5rem 0 0', fontSize: '0.8125rem', color: '#93c5fd' }}>
                Nouveau bootstrap lancé, vous pouvez fermer cette fenêtre.
              </p>
            )}
          </div>
        )}

        {step === 'DONE' && (
          <div style={{ marginTop: '1rem', padding: '0.5rem', backgroundColor: '#064e3b', borderRadius: '0.25rem', color: '#6ee7b7', textAlign: 'center' }}>ModelWeaver est en cours d'exécution. Vous pouvez fermer cette fenêtre.</div>
        )}
        {step === 'ERROR' && (
          <div style={{ marginTop: '1rem', padding: '0.5rem', backgroundColor: '#450a0a', borderRadius: '0.25rem', color: '#fca5a5', textAlign: 'center' }}>{error}</div>
        )}
      </div>
    </div>
  );
}

export default App;
