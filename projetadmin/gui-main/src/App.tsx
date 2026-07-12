import React, { useState, useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';

interface Dependency {
  name: string;
  description: string;
  check_command: string;
  version_regex: string;
  min_version: string;
  install_commands?: Record<string, string>;
  installed?: boolean;
  version?: string;
  error?: string;
}

interface PackageManager {
  available: boolean;
  description: string;
}

interface PythonPackageManager extends PackageManager {
  version?: string;
}

// Global styles for select dropdowns
const selectStyles = {
  backgroundColor: '#1e293b',
  color: '#e2e8f0',
  border: '1px solid #475569',
  borderRadius: '0.25rem',
  fontSize: '0.75rem',
  outline: 'none',
  boxShadow: '0 0 0 1px rgba(59, 130, 246, 0.5)',
  appearance: 'none',
  WebkitAppearance: 'none',
  MozAppearance: 'none',
};

function App() {
  const [requiredDeps, setRequiredDeps] = useState<Dependency[]>([]);
  const [recommendedDeps, setRecommendedDeps] = useState<Dependency[]>([]);
  const [packageManagers, setPackageManagers] = useState<Record<string, PackageManager>>({});
  const [pythonPackageManagers, setPythonPackageManagers] = useState<Record<string, PythonPackageManager>>({});
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showDashboard, setShowDashboard] = useState(false);
  const [os, setOs] = useState<string>('');
  const [selectedPms, setSelectedPms] = useState<Record<string, string>>({});
  const [selectedRecommended, setSelectedRecommended] = useState<Record<string, boolean>>({});

  const [installing, setInstalling] = useState(false);
  const [autoInstallTimer, setAutoInstallTimer] = useState<number | null>(null);
  const [installLog, setInstallLog] = useState<string[]>([]);
  const [installProgress, setInstallProgress] = useState<{ name: string; status: 'pending' | 'installing' | 'success' | 'failed'; detail: string }[]>([]);
  const [installPanelOpen, setInstallPanelOpen] = useState(false);

  // Logithèque state
  const [systemState, setSystemState] = useState<any>(null);
  const [catalogueTools, setCatalogueTools] = useState<any[]>([]);
  const [installedTools, setInstalledTools] = useState<any[]>([]);
  const [logithequeLoading, setLogithequeLoading] = useState(false);
  const [logithequeError, setLogithequeError] = useState<string | null>(null);
  const [installQueue, setInstallQueue] = useState<{ id: number; ref: string; name: string; job_type: string; status: string; log: string }[]>([]);

  // Debug / process manager panel
  const [showDebug, setShowDebug] = useState(false);
  const [showKeys, setShowKeys] = useState(false);
  const [keysList, setKeysList] = useState<any[]>([]);
  const [keysNewProvider, setKeysNewProvider] = useState('');
  const [keysNewValue, setKeysNewValue] = useState('');
  const [keysNewTag, setKeysNewTag] = useState<'free' | 'paid'>('free');
  const [debugTab, setDebugTab] = useState<'process' | 'logs' | 'resources'>('process');
  const [procList, setProcList] = useState<{ id: number; name: string; pid: number | null; parent_id: number | null; status: string; command: string; log_path: string; cpu: number; rss_kb: number; started_at: number; ended_at: number | null }[]>([]);
  const [procLogId, setProcLogId] = useState<number | null>(null);
  const [procLogText, setProcLogText] = useState<string>('');
  const [serviceList, setServiceList] = useState<{ name: string; mode: string; status: string; pid: number | null; restarts: number; last_exit: number | null; started_at: number }[]>([]);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const setDebug = (v: boolean) => {
    setShowDebug(v);
    getCurrentWindow().setSize(new LogicalSize(v ? 1380 : 1000, v ? 760 : 700)).catch(() => {});
  };

  const maskKey = (k: string) => k.length > 4 ? k.slice(0, 2) + '****' + k.slice(-2) : '****';

  const fetchKeys = async () => {
    try {
      const token = await invoke<string>('watch_get', { name: 'api_token' });
      if (!token) return;
      const res = await fetch('http://127.0.0.1:8770/v1/keys/list', {
        method: 'POST', headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }, body: '{}'
      });
      const data = await res.json();
      if (data.ok) setKeysList(data.result.keys || []);
    } catch { /* daemon pas encore prêt */ }
  };

  const handleSetKey = async () => {
    if (!keysNewProvider || !keysNewValue) return;
    try {
      const token = await invoke<string>('watch_get', { name: 'api_token' });
      if (!token) return;
      await fetch('http://127.0.0.1:8770/v1/keys/set', {
        method: 'POST', headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider_ref: keysNewProvider, api_key: keysNewValue, tag: keysNewTag })
      });
      setKeysNewProvider(''); setKeysNewValue(''); setKeysNewTag('free');
      await fetchKeys();
    } catch { /* ignore */ }
  };

  const handleDeleteKey = async (providerRef: string) => {
    try {
      const token = await invoke<string>('watch_get', { name: 'api_token' });
      if (!token) return;
      await fetch('http://127.0.0.1:8770/v1/keys/delete', {
        method: 'POST', headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider_ref: providerRef })
      });
      await fetchKeys();
    } catch { /* ignore */ }
  };

  const handleToggleLock = async (ref: string, locked: boolean) => {
    try {
      const token = await invoke<string>('watch_get', { name: 'api_token' });
      if (!token) return;
      await fetch('http://127.0.0.1:8770/v1/keys/set_lock', {
        method: 'POST', headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, locked: !locked })
      });
      await fetchKeys();
    } catch { /* ignore */ }
  };

  const toggleFullscreen = () => {
    getCurrentWindow().setFullscreen(!isFullscreen).then(() => setIsFullscreen(!isFullscreen)).catch(() => {});
  };

  const fetchProcList = () => invoke<any[]>('process_list').then(setProcList).catch(() => {});
  const fetchProcLog = (id: number) => {
    setProcLogId(id);
    invoke<string>('process_log', { id }).then(setProcLogText).catch(() => setProcLogText(''));
  };
  const fetchServiceList = () => invoke<any[]>('service_list').then(setServiceList).catch(() => {});

  useEffect(() => {
    if (!showDebug) return;
    fetchProcList();
    fetchServiceList();
    const t = setInterval(() => { fetchProcList(); fetchServiceList(); }, 1000);
    return () => clearInterval(t);
  }, [showDebug]);
  const [installListOpen, setInstallListOpen] = useState(true);

  const installedRef = useRef<any[]>([]);
  const installQueueRef = useRef<{ id: number; ref: string; name: string; job_type: string; status: string; log: string }[]>([]);
  const prevStatusRef = useRef<Record<number, string>>({});
  const autoInstalledRef = useRef(false);
  const CATALOGUE_URL = 'http://localhost:8765/api';

  // Refs for fresh data inside async callbacks (setState is async, setTimeout closures go stale)
  const requiredDepsRef = useRef<Dependency[]>([]);
  const recommendedDepsRef = useRef<Dependency[]>([]);
  const selectedPmsRef = useRef<Record<string, string>>({});
  const selectedRecommendedRef = useRef<Record<string, boolean>>({});

  const addLog = (msg: string) => {
    const ts = new Date().toISOString().split('T')[1]?.split('.')[0] || '';
    const line = `[${ts}] ${msg}`;
    setInstallLog(prev => [...prev, line]);
    invoke('log_message', { level: 'INSTALL', message: msg }).catch(() => {});
  };

  useEffect(() => {
    checkDependencies();
    return () => { if (autoInstallTimer) clearTimeout(autoInstallTimer); };
  }, []);

  useEffect(() => {
    if (showDashboard) {
      loadLogitheque();
    }
    return () => {
      installQueueRef.current = [];
    };
  }, [showDashboard]);

  // Polling de la file d'installation (thread Rust dédié) : non bloquant pour le reste de la GUI
  useEffect(() => {
    if (!showDashboard) return;
    const poll = async () => {
      try {
        const status = await invoke<any[]>('install_queue_status');
        setInstallQueue(status);
        installQueueRef.current = status;
        await refreshInstalled();
        // Détecter les transitions vers un état terminal pour rafraîchir le catalogue
        let changed = false;
        for (const j of status) {
          const prev = prevStatusRef.current[j.id];
          if (prev && prev !== j.status && (j.status === 'installed' || j.status === 'failed' || j.status === 'removed' || j.status === 'cancelled')) {
            changed = true;
          }
          prevStatusRef.current[j.id] = j.status;
        }
        if (changed) {
          const cat = await invoke<any>('get_catalogue_tools');
          setCatalogueTools(cat.tools || []);
        }
      } catch (e) {
        // worker indisponible : on ignore
      }
    };
    const h = setInterval(poll, 1000);
    poll();
    return () => clearInterval(h);
  }, [showDashboard]);

  const checkDependencies = async () => {
    setChecking(true);
    setError(null);
    try {
      // Timeout after 30 seconds
      const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => {
          reject(new Error("Dependency check timed out after 30 seconds"));
        }, 30000);
      });
      
      await Promise.race([
        (async () => {
      // Get system info (OS)
      const systemOs: string = await invoke('get_platform');
      setOs(systemOs);
      
      // Load platform-specific dependencies
      let dependenciesData;
      if (systemOs === 'linux') {
        dependenciesData = await import('./dependencies/dependencies_linux.json');
      } else {
        throw new Error(`Unsupported OS: ${systemOs}`);
      }
      
      // Check dependencies using Rust backend
      const result = await invoke<{
        [key: string]: {
          installed?: boolean;
          version?: string;
          error?: string;
        };
        package_managers?: Record<string, PackageManager>;
        python_package_managers?: Record<string, PythonPackageManager>;
      }>('check_dependencies_with_config', { config: dependenciesData });
      
      // Update required dependencies
      const requiredDepsList: Dependency[] = dependenciesData.required.map((dep: any) => ({
        ...dep,
        ...result[dep.name],
      }));
      setRequiredDeps(requiredDepsList);
      requiredDepsRef.current = requiredDepsList;
      
      // Update recommended dependencies
      const recommendedDepsList: Dependency[] = dependenciesData.recommended.map((dep: any) => ({
        ...dep,
        ...result[dep.name],
      }));
      setRecommendedDeps(recommendedDepsList);
      recommendedDepsRef.current = recommendedDepsList;
      
      // Initialize selected recommended (all checked by default)
      const initialSelectedRecommended: Record<string, boolean> = {};
      dependenciesData.recommended.forEach((dep: any) => {
        initialSelectedRecommended[dep.name] = true;
      });
      setSelectedRecommended(initialSelectedRecommended);
      selectedRecommendedRef.current = initialSelectedRecommended;
      
      // Initialize selected package managers (default to first available)
      const pmAvailable = result.package_managers || {};
      const initialSelectedPms: Record<string, string> = {};
      requiredDepsList.concat(recommendedDepsList).forEach(dep => {
        if (!dep.installed && dep.install_commands) {
          const availablePms = Object.keys(dep.install_commands).filter(pm => pmAvailable[pm]?.available);
          if (availablePms.length > 0) {
            initialSelectedPms[dep.name] = availablePms[0];
          }
        }
      });
      setSelectedPms(initialSelectedPms);
      selectedPmsRef.current = initialSelectedPms;
      
      if (result.package_managers) {
        setPackageManagers(result.package_managers);
      }
      
      if (result.python_package_managers) {
        setPythonPackageManagers(result.python_package_managers);
      }
      
      // Show dashboard if all required dependencies are installed
      if (requiredDepsList.every(dep => dep.installed)) {
        setShowDashboard(true);
      } else {
        addLog(`Missing dependencies detected: ${requiredDepsList.filter(d => !d.installed).map(d => d.name).join(', ')}`);
        addLog('Auto-install will start in 10 seconds...');
        const timer = setTimeout(() => {
          addLog('Auto-install triggered');
          handleInstall();
        }, 10000);
        setAutoInstallTimer(timer);
      }
      })(),
        timeoutPromise
      ]);
    } catch (err) {
      setError(`Failed to check dependencies: ${err}`);
    } finally {
      setChecking(false);
    }
  };

  const handleRecommendedToggle = (name: string) => {
    setSelectedRecommended(prev => {
      const next = { ...prev, [name]: !prev[name] };
      selectedRecommendedRef.current = next;
      return next;
    });
  };

  const handlePmChange = (depName: string, pm: string) => {
    setSelectedPms(prev => {
      const next = { ...prev, [depName]: pm };
      selectedPmsRef.current = next;
      return next;
    });
  };

  const updateProgress = (name: string, status: 'pending' | 'installing' | 'success' | 'failed', detail: string) => {
    setInstallProgress(prev => {
      const idx = prev.findIndex(p => p.name === name);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { name, status, detail };
        return next;
      }
      return [...prev, { name, status, detail }];
    });
  };

  const handleInstall = async () => {
    if (installing) return;
    setInstalling(true);
    setInstallPanelOpen(true);
    if (autoInstallTimer) { clearTimeout(autoInstallTimer); setAutoInstallTimer(null); }
    setInstallProgress([]);
    addLog('=== Starting installation ===');

    const reqs = requiredDepsRef.current;
    const recs = recommendedDepsRef.current;
    const selPms = selectedPmsRef.current;
    const selRec = selectedRecommendedRef.current;

    const plan: { dep: Dependency; required: boolean }[] = [];
    for (const dep of reqs) {
      if (!dep.installed && dep.install_commands) plan.push({ dep, required: true });
      else if (!dep.installed) addLog(`Skipping ${dep.name} (no install_commands)`);
    }
    for (const dep of recs) {
      if (!dep.installed && selRec[dep.name] && dep.install_commands) plan.push({ dep, required: false });
    }

    try {
      addLog(`Installing ${plan.length} dependencies...`);
      for (const { dep, required } of plan) {
        const pm = selPms[dep.name] || 'apt';
        const cmd = dep.install_commands?.[pm];
        if (!cmd) { addLog(`No install command for ${dep.name}`); continue; }
        // Strip "sudo " prefix if running as root in Docker
        const actualCmd = cmd.replace(/^sudo\s+/, '');
        updateProgress(dep.name, 'installing', `via ${pm}...`);
        addLog(`Installing ${dep.name}${required ? '' : ' (recommended)'} via ${pm}: ${actualCmd}`);
        try {
          const result = await invoke<{ stdout: string; stderr: string; success: boolean }>('run_command', {
            command: 'bash',
            args: ['-c', actualCmd],
          });
          if (result.success) {
            updateProgress(dep.name, 'success', result.stdout.trim().substring(0, 200) || 'OK');
            addLog(`  ${dep.name}: SUCCESS`);
          } else {
            updateProgress(dep.name, 'failed', (result.stderr || '').trim().substring(0, 200) || 'FAILED');
            addLog(`  ${dep.name}: FAILED`);
          }
        } catch (err: any) {
          updateProgress(dep.name, 'failed', String(err).substring(0, 200));
          addLog(`  ${dep.name}: ERROR - ${err}`);
        }
      }

      addLog('=== Installation complete, re-checking dependencies ===');
      await checkDependencies();
    } catch (err) {
      addLog(`Installation error: ${err}`);
      setError(`Failed to install dependencies: ${err}`);
    } finally {
      setInstalling(false);
    }
  };

  const handleQuit = () => {
    invoke('close_splashscreen'); // Tauri command to close the window
  };

  // ── Logithèque ──

  const refreshInstalled = async () => {
    try {
      const cached = await invoke<string>('watch_get', { name: 'installed-tools' });
      if (cached) {
        const inst = JSON.parse(cached);
        setInstalledTools(inst.tools || []);
        installedRef.current = inst.tools || [];
        return;
      }
    } catch { /* fallthrough */ }
    const inst = await invoke<any>('get_installed_tools');
    setInstalledTools(inst.tools || []);
    installedRef.current = inst.tools || [];
  };

  const refreshSysState = async () => {
    try {
      const cached = await invoke<string>('watch_get', { name: 'sys-state' });
      if (cached) { setSystemState(JSON.parse(cached)); return; }
    } catch { /* fallthrough */ }
    setSystemState(await invoke<any>('get_system_state'));
  };

  const loadLogitheque = async () => {
    setLogithequeLoading(true);
    setLogithequeError(null);
    try {
      addLog('[LOGITH] start');
      await invoke('init_databases'); addLog('[LOGITH] init_databases ok');
      await invoke('seed_catalogue'); addLog('[LOGITH] seed_catalogue ok');
      await invoke('save_system_state'); addLog('[LOGITH] save_system_state ok');
      await refreshSysState(); addLog('[LOGITH] refreshSysState ok');
      const cat = await invoke<any>('get_catalogue_tools'); addLog('[LOGITH] get_catalogue_tools ok');
      setCatalogueTools(cat.tools || []);
      await refreshInstalled(); addLog('[LOGITH] refreshInstalled ok');
      addLog('Logithèque chargée (catalogue local)');
      // Auto-install tous les outils non installés (une seule fois au premier chargement)
      if (!autoInstalledRef.current) {
        autoInstalledRef.current = true;
        addLog('Déclenchement auto-install de tous les outils...');
        invoke<any>('install_all_tools')
          .then((r: any) => addLog(`Auto-install résultat: ${JSON.stringify(r)}`))
          .catch((e: any) => addLog(`Auto-install erreur: ${e}`));
      }
      // Sync distante ASYNCHRONE : ne bloque pas l'UI
      invoke<any>('sync_catalogue', { url: CATALOGUE_URL })
        .then(async (r: any) => {
          addLog(`Sync catalogue distant OK: ${JSON.stringify(r.results)}`);
          const cat2 = await invoke<any>('get_catalogue_tools');
          setCatalogueTools(cat2.tools || []);
        })
        .catch((e: any) => {
          addLog(`Sync catalogue distant impossible (hors-ligne?): ${e}`);
        });
    } catch (err: any) {
      addLog(`[LOGITH] ERROR: ${err}`);
      setLogithequeError(`Logithèque: ${err}`);
    } finally {
      setLogithequeLoading(false);
    }
  };

  const handleUninstallTool = (ref: string, name: string) => {
    addLog(`File: demande de désinstallation de ${name} (${ref})`);
    invoke('install_queue_add', { ref, name, jobType: 'uninstall' })
      .then((id) => { if (!id) addLog(`${name} déjà en cours ou en file`); })
      .catch((e: any) => addLog(`  ${name}: ERREUR file ${e}`));
  };

  const handleAddToInstallList = (ref: string, name: string) => {
    const deja = installQueueRef.current.some(q => q.ref === ref && (q.status === 'queued' || q.status === 'running'));
    if (deja) {
      addLog(`${name} déjà dans la file`);
      return;
    }
    addLog(`Ajout de ${name} à la file d'installation (séquentielle, thread dédié)`);
    invoke<number>('install_queue_add', { ref, name, jobType: 'install' })
      .then((id) => { if (!id) addLog(`${name} déjà en cours ou en file`); })
      .catch((e: any) => addLog(`  ${name}: ERREUR file ${e}`));
  };

  const handleCancelInstall = (id: number, name: string) => {
    addLog(`Annulation de ${name} (job #${id})`);
    invoke('install_queue_cancel', { id }).catch((e: any) => addLog(`  cancel ERREUR ${e}`));
  };

  const handleClearQueue = () => {
    invoke('install_queue_clear').catch(() => {});
  };

  const allRequiredInstalled = requiredDeps.every(dep => dep.installed);

  if (showDashboard) {
    const isInstalled = (ref: string) => installedRef.current.some(t => t.ref === ref);
    const queueJob = (ref: string) => installQueueRef.current.find(q => q.ref === ref);
    const isQueued = (ref: string) => { const j = queueJob(ref); return !!j && (j.status === 'queued' || j.status === 'running'); };
    const fmtGb = (v: any) => (v == null ? 'n/a' : `${v} Go`);

    return (
      <div style={{
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: '#0f172a',
        color: '#e2e8f0',
        fontFamily: 'sans-serif',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          padding: '1rem 1.5rem',
          borderBottom: '1px solid #334155',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div>
            <h1 style={{ fontSize: '1.25rem', fontWeight: 'bold' }}>ModelWeaver Logithèque</h1>
            <div style={{ fontSize: '0.7rem', color: '#64748b' }}>
              {logithequeLoading ? 'Chargement...' : 'Catalogue local + sync distante async'}
            </div>
          </div>
          <button
            onClick={async () => { setCatalogueTools([]); await refreshInstalled(); loadLogitheque(); }}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
          >
            ↻ Rafraîchir
          </button>
          <button
            onClick={async () => {
              addLog('Installation automatique de tous les outils...');
              try { const r = await invoke<any>('install_all_tools'); addLog(`Résultat: ${JSON.stringify(r)}`); } catch (e) { addLog(`Erreur install all: ${e}`); }
            }}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: '#059669', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem', fontWeight: '600' }}
          >
            ⚡ Tout installer
          </button>
          <button
            onClick={() => setDebug(!showDebug)}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: showDebug ? '#2563eb' : '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
          >
            🐞 Debug
          </button>
          <button
            onClick={() => { setShowKeys(!showKeys); if (!showKeys) fetchKeys(); }}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: showKeys ? '#7c3aed' : '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
          >
            🔑 Clés
          </button>
          <button
            onClick={toggleFullscreen}
            style={{ padding: '0.4rem 0.6rem', backgroundColor: '#334155', color: '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.75rem' }}
            title="Plein écran"
          >
            {isFullscreen ? '🗗' : '⛶'}
          </button>
        </div>

        {logithequeError && (
          <div style={{ color: '#fca5a5', padding: '0.5rem 1.5rem', fontSize: '0.8rem' }}>Erreur: {logithequeError}</div>
        )}

        {/* Body */}
        <div style={{ flex: 1, display: 'flex', gap: '1rem', padding: '1rem 1.5rem', overflow: 'hidden' }}>

          {/* Left: system state + installed */}
          <div style={{ width: '340px', display: 'flex', flexDirection: 'column', gap: '1rem', overflowY: 'auto' }}>
            <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.75rem' }}>État du système</h3>
              {systemState ? (
                <div style={{ fontSize: '0.78rem', lineHeight: '1.7' }}>
                  <div><span style={{ color: '#64748b' }}>OS :</span> {systemState.os} {systemState.os_version || ''}</div>
                  <div><span style={{ color: '#64748b' }}>Arch :</span> {systemState.architecture}</div>
                  <div><span style={{ color: '#64748b' }}>RAM :</span> {fmtGb(systemState.ram_total_gb)} <span style={{ color: '#64748b' }}>(libre {fmtGb(systemState.ram_available_gb)})</span></div>
                  <div><span style={{ color: '#64748b' }}>Disque :</span> {fmtGb(systemState.disk_total_gb)} <span style={{ color: '#64748b' }}>(libre {fmtGb(systemState.disk_free_gb)})</span></div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <span style={{ color: '#64748b' }}>Gestionnaires :</span>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', marginTop: '0.3rem' }}>
                      {(systemState.detected_managers || []).map((pm: string) => (
                        <span key={pm} style={{ fontSize: '0.65rem', backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.25rem', padding: '0.1rem 0.4rem', color: '#6ee7b7' }}>{pm}</span>
                      ))}
                      {(systemState.detected_managers || []).length === 0 && <span style={{ color: '#fca5a5' }}>aucun</span>}
                    </div>
                  </div>
                </div>
              ) : <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>…</div>}
            </div>

            <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.75rem' }}>Outils installés ({installedTools.length})</h3>
              {installedTools.length === 0 ? (
                <div style={{ color: '#94a3b8', fontSize: '0.78rem' }}>Aucun outil détecté</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  {installedTools.map((t: any) => (
                    <div key={t.ref} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem', borderBottom: '1px solid #334155', paddingBottom: '0.3rem' }}>
                      <span style={{ fontWeight: '500' }}>{t.name || t.ref} <span style={{ color: '#6ee7b7' }}>{t.version || ''}</span></span>
                      <button
                        onClick={async () => await handleUninstallTool(t.ref, t.name || t.ref)}
                        style={{ backgroundColor: '#7f1d1d', color: '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.25rem', padding: '0.1rem 0.5rem', fontSize: '0.7rem', cursor: 'pointer' }}
                      >
                        Uninstall
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right: catalogue + install queue */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem', overflow: 'hidden' }}>
            <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem', flex: 1, overflowY: 'auto' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.75rem' }}>Catalogue d'outils ({catalogueTools.length})</h3>
              {catalogueTools.length === 0 ? (
                <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Chargement du catalogue…</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                  {catalogueTools.map((t: any) => {
                    const inst = isInstalled(t.ref);
                    const queued = isQueued(t.ref);
                    return (
                      <div key={t.ref} style={{ backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.375rem', padding: '0.75rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ maxWidth: '70%' }}>
                          <div style={{ fontWeight: '600', fontSize: '0.85rem' }}>{t.name} <span style={{ fontSize: '0.6rem', color: '#64748b' }}>({t.ref})</span></div>
                          <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>{t.description}</div>
                          <div style={{ marginTop: '0.25rem' }}>
                            <span style={{ fontSize: '0.6rem', backgroundColor: '#1e293b', border: '1px solid #475569', borderRadius: '0.25rem', padding: '0.05rem 0.35rem', color: '#93c5fd' }}>{t.tool_type}</span>
                            <span style={{ fontSize: '0.6rem', backgroundColor: '#1e293b', border: '1px solid #475569', borderRadius: '0.25rem', padding: '0.05rem 0.35rem', color: '#cbd5e1', marginLeft: '0.3rem' }}>{t.install_method}</span>
                          </div>
                        </div>
                        <div>
                          {inst ? (
                            <span style={{ color: '#6ee7b7', fontSize: '0.75rem', fontWeight: '600' }}>✓ Installé</span>
                          ) : (() => {
                            const j = queueJob(t.ref);
                            if (j && (j.status === 'queued' || j.status === 'running')) {
                              const label = j.status === 'running' ? '⏳ En cours' : '🕓 En attente';
                              return (
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
                                  <span style={{ color: '#fbbf24', fontSize: '0.75rem' }}>{label}</span>
                                  <button
                                    onClick={() => handleCancelInstall(j.id, t.name)}
                                    style={{ padding: '0.25rem 0.55rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.7rem' }}
                                  >Annuler</button>
                                </span>
                              );
                            }
                            if (j && (j.status === 'installed' || j.status === 'removed')) {
                              const txt = j.status === 'removed' ? '✓ Retiré' : '✓ Installé';
                              return (
                                <span style={{ color: '#6ee7b7', fontSize: '0.75rem', fontWeight: '600' }}>{txt}</span>
                              );
                            }
                            return (
                              <button
                                onClick={() => handleAddToInstallList(t.ref, t.name)}
                                style={{ padding: '0.4rem 0.7rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: 'pointer', fontSize: '0.72rem', fontWeight: '500' }}
                              >
                                + Add to install list
                              </button>
                            );
                          })()}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Install queue */}
            <div style={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '0.375rem', overflow: 'hidden' }}>
              <button
                onClick={() => setInstallListOpen(!installListOpen)}
                style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.6rem 1rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: 'none', cursor: 'pointer', fontSize: '0.8rem', fontWeight: '600' }}
              >
                <span>File d'installation ({installQueue.filter(q => q.status === 'installed' || q.status === 'removed').length}/{installQueue.length})</span>
                <span style={{ transform: installListOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▾</span>
              </button>
              {installListOpen && (
                <div style={{ padding: '0 1rem 0.75rem' }}>
                  {installQueue.length === 0 ? (
                    <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun outil en attente — ajoutez des outils du catalogue</div>
                  ) : (
                    <>
                      {installQueue.map((q) => {
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
                                onClick={() => handleCancelInstall(q.id, q.name)}
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
                        onClick={handleClearQueue}
                        style={{ marginTop: '0.5rem', padding: '0.25rem 0.6rem', backgroundColor: '#334155', color: '#cbd5e1', border: '1px solid #475569', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.7rem' }}
                      >Vider la file (terminés)</button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
          {showKeys && (
            <div style={{ width: '340px', display: 'flex', flexDirection: 'column', gap: '0.75rem', overflow: 'hidden', borderLeft: '1px solid #334155', paddingLeft: '1rem' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: '600' }}>🔑 Clés API ({keysList.length})</h3>
              <div style={{ flex: 1, overflowY: 'auto', backgroundColor: '#1e293b', borderRadius: '0.375rem', border: '1px solid #334155', padding: '0.6rem' }}>
                {/* Ajout d'une clé */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginBottom: '0.75rem', padding: '0.5rem', backgroundColor: '#0f172a', borderRadius: '0.375rem' }}>
                  <input
                    placeholder="Provider (ex: openai)"
                    value={keysNewProvider}
                    onChange={e => setKeysNewProvider(e.target.value)}
                    style={{ padding: '0.3rem 0.5rem', fontSize: '0.72rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', outline: 'none' }}
                  />
                  <input
                    placeholder="Clé API"
                    value={keysNewValue}
                    onChange={e => setKeysNewValue(e.target.value)}
                    style={{ padding: '0.3rem 0.5rem', fontSize: '0.72rem', backgroundColor: '#1e293b', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '0.25rem', outline: 'none' }}
                  />
                  <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                    <select
                      value={keysNewTag}
                      onChange={e => setKeysNewTag(e.target.value as 'free' | 'paid')}
                      style={{ flex: 1, ...selectStyles, fontSize: '0.72rem', padding: '0.3rem' }}
                    >
                      <option value="free">Free</option>
                      <option value="paid">Paid</option>
                    </select>
                    <button
                      onClick={handleSetKey}
                      style={{ padding: '0.3rem 0.7rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.3rem', cursor: 'pointer', fontSize: '0.72rem', fontWeight: '600' }}
                    >+</button>
                  </div>
                </div>
                {/* Liste des clés */}
                {keysList.length === 0 ? (
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucune clé enregistrée</div>
                ) : (
                  keysList.map((k: any) => (
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
                            onChange={() => handleToggleLock(k.ref, k.locked)}
                            style={{ cursor: 'pointer', width: '0.9rem', height: '0.9rem', accentColor: '#3b82f6' }}
                          />
                        </label>
                        <button
                          onClick={() => handleDeleteKey(k.provider_ref)}
                          style={{ padding: '0.15rem 0.4rem', backgroundColor: '#7f1d1d', color: '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.25rem', cursor: 'pointer', fontSize: '0.65rem' }}
                          >✕</button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
          {showDebug && (
            <div style={{ width: '360px', display: 'flex', flexDirection: 'column', gap: '0.75rem', overflow: 'hidden', borderLeft: '1px solid #334155', paddingLeft: '1rem' }}>
              {/* Bandeau de panneaux supplémentaires */}
              <div style={{ display: 'flex', gap: '0.4rem' }}>
                {(['process', 'services', 'logs', 'resources'] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setDebugTab(tab)}
                    style={{ flex: 1, padding: '0.35rem', fontSize: '0.7rem', borderRadius: '0.3rem', cursor: 'pointer', backgroundColor: debugTab === tab ? '#2563eb' : '#334155', color: '#e2e8f0', border: 'none' }}
                  >
                    {tab === 'process' ? 'Processus' : tab === 'services' ? 'Services' : tab === 'logs' ? 'Logs' : 'Ressources'}
                  </button>
                ))}
              </div>
              <div style={{ flex: 1, overflowY: 'auto', backgroundColor: '#1e293b', borderRadius: '0.375rem', border: '1px solid #334155', padding: '0.6rem' }}>
                {debugTab === 'process' && (
                  <>
                    <div style={{ fontSize: '0.72rem', color: '#64748b', marginBottom: '0.5rem' }}>Arbre des processus (tick 1 Hz)</div>
                    {procList.length === 0 ? (
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun processus suivi</div>
                    ) : (
                      procList.map((p) => {
                        const byId: any = {};
                        procList.forEach((x) => { byId[x.id] = x; });
                        let d = 0; let cur = p.parent_id;
                        while (cur && byId[cur]) { d++; cur = byId[cur].parent_id; }
                        const stColor = p.status === 'running' ? '#6ee7b7' : p.status === 'failed' ? '#f87171' : p.status === 'cancelled' ? '#fbbf24' : '#94a3b8';
                        return (
                          <div key={p.id} style={{ marginLeft: d * 14, padding: '0.3rem 0', borderBottom: '1px solid #334155', fontSize: '0.72rem' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                              <span style={{ fontWeight: '600', color: '#e2e8f0' }}>{p.name}</span>
                              <span style={{ color: stColor }}>● {p.status}</span>
                              {p.pid ? <span style={{ color: '#64748b' }}>pid {p.pid}</span> : null}
                              <span style={{ color: '#64748b', marginLeft: 'auto' }}>{p.cpu.toFixed(1)}% · {(p.rss_kb / 1024).toFixed(0)} Mo</span>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.15rem' }}>
                              <span style={{ color: '#64748b', fontSize: '0.65rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '70%' }}>{p.command}</span>
                              <button onClick={() => fetchProcLog(p.id)} style={{ marginLeft: 'auto', fontSize: '0.62rem', padding: '0.1rem 0.4rem', backgroundColor: '#334155', color: '#cbd5e1', border: '1px solid #475569', borderRadius: '0.25rem', cursor: 'pointer' }}>logs</button>
                            </div>
                            {procLogId === p.id && (
                              <pre style={{ marginTop: '0.3rem', maxHeight: '160px', overflowY: 'auto', backgroundColor: '#0f172a', color: '#a5b4fc', fontSize: '0.62rem', padding: '0.4rem', borderRadius: '0.25rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{procLogText || '(vide)'}</pre>
                            )}
                          </div>
                        );
                      })
                    )}
                  </>
                )}
                {debugTab === 'services' && (
                  <>
                    <div style={{ fontSize: '0.72rem', color: '#64748b', marginBottom: '0.5rem' }}>Services supervisés (auto-redémarrage si crash)</div>
                    {serviceList.length === 0 ? (
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun service déclaré</div>
                    ) : (
                      serviceList.map((s) => {
                        const stColor = s.status === 'running' ? '#6ee7b7' : s.status === 'restarting' ? '#fbbf24' : '#f87171';
                        return (
                          <div key={s.name} style={{ padding: '0.3rem 0', borderBottom: '1px solid #334155', fontSize: '0.72rem' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                              <span style={{ fontWeight: '600', color: '#e2e8f0' }}>{s.name}</span>
                              <span style={{ color: stColor }}>● {s.status}</span>
                              {s.pid ? <span style={{ color: '#64748b' }}>pid {s.pid}</span> : null}
                              <span style={{ color: '#64748b', marginLeft: 'auto' }}>↻ {s.restarts}</span>
                            </div>
                            <div style={{ color: '#64748b', fontSize: '0.65rem', marginTop: '0.1rem' }}>
                              {s.mode}{s.last_exit != null ? ` · exit ${s.last_exit}` : ''} · démarré {new Date(s.started_at * 1000).toLocaleTimeString()}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </>
                )}
                {debugTab === 'logs' && (
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Cliquez sur « logs » d'un processus de l'onglet Processus pour voir sa sortie disque.</div>
                )}
                {debugTab === 'resources' && (
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Vue ressources globale (CPU/RAM/bande passante agrégée) — à venir.</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: '#0f172a',
      color: '#e2e8f0',
      fontFamily: 'sans-serif',
      padding: '2rem',
    }}>
      <div style={{ maxWidth: '800px', width: '100%', margin: '0 auto' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 'bold', marginBottom: '2rem' }}>
          ModelWeaver
        </h1>

        {/* Dependencies panel */}
        <div style={{
          backgroundColor: '#1e293b',
          borderRadius: '0.5rem',
          border: '1px solid #334155',
          padding: '1.5rem',
          marginBottom: '1.5rem',
        }}>
          <h3 style={{ fontSize: '1rem', fontWeight: '600', marginBottom: '1rem' }}>System Dependencies</h3>

          {checking && <p style={{ color: '#94a3b8' }}>Checking dependencies...</p>}

          {!checking && error && (
            <div style={{ color: '#fca5a5', marginBottom: '1rem' }}>Error: {error}</div>
          )}

          {/* Required dependencies */}
          <div style={{ marginBottom: recommendedDeps.length > 0 ? '1.5rem' : '0' }}>
            <h4 style={{ fontSize: '0.875rem', fontWeight: '600', color: '#94a3b8', marginBottom: '0.75rem' }}>Required</h4>
            {requiredDeps.map((dep) => (
              <div key={dep.name} style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.5rem 0',
                borderBottom: '1px solid #334155',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <input
                    type="checkbox"
                    checked
                    disabled

                    style={{
                      width: '16px',
                      height: '16px',
                      cursor: 'not-allowed',
                      opacity: 1,
                    }}
                  />
                  <div>
                    <div style={{ fontWeight: '500' }}>{dep.name}</div>
                    <div style={{ fontSize: '0.75rem', color: '#64748b' }}>{dep.description}</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {dep.installed ? (
                    <span style={{ color: '#6ee7b7' }}>✓ {dep.version}</span>
                  ) : (
                    <>
                      {dep.install_commands && Object.keys(dep.install_commands).length > 0 && (
                          <select
                            value={selectedPms[dep.name] || ''}
                            onChange={(e) => handlePmChange(dep.name, e.target.value)}
                            style={{ ...selectStyles, padding: '0.25rem 0.5rem' }}
                          >
                            {Object.entries(dep.install_commands).map(([pm, _]) => (
                              packageManagers[pm]?.available && (
                                <option key={pm} value={pm}>{pm}</option>
                              )
                            ))}
                          </select>
                      )}
                      <span style={{ color: dep.install_commands ? '#f59e0b' : '#fca5a5' }}>
                        {dep.install_commands ? '⚠ To install' : '✗ Missing'}
                      </span>
                      {dep.error && dep.install_commands && <span style={{ fontSize: '0.75rem', color: '#f59e0b' }}> (Will be installed)</span>}
                      {dep.error && !dep.install_commands && <span style={{ fontSize: '0.75rem', color: '#fca5a5' }}> ({dep.error})</span>}
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Recommended dependencies */}
          {recommendedDeps.length > 0 && (
            <div>
              <h4 style={{ fontSize: '0.875rem', fontWeight: '600', color: '#94a3b8', marginBottom: '0.75rem' }}>Recommended</h4>
              {recommendedDeps.map((dep) => (
                <div key={dep.name} style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '0.5rem 0',
                  borderBottom: '1px solid #334155',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <input
                      type="checkbox"
                      checked={dep.installed || selectedRecommended[dep.name] || false}
                      onChange={() => handleRecommendedToggle(dep.name)}
                      disabled={dep.installed}
                      style={{
                        width: '16px',
                        height: '16px',
                        cursor: dep.installed ? 'not-allowed' : 'pointer',
                        opacity: dep.installed ? 1 : 0.8,
                      }}
                    />
                    <div>
                      <div style={{ fontWeight: '500' }}>{dep.name}</div>
                      <div style={{ fontSize: '0.75rem', color: '#64748b' }}>{dep.description}</div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {dep.installed ? (
                      <span style={{ color: '#6ee7b7' }}>✓ {dep.version}</span>
                    ) : (
                      <>
                      {dep.install_commands && Object.keys(dep.install_commands).length > 0 && (
                          <select
                            value={selectedPms[dep.name] || ''}
                            onChange={(e) => handlePmChange(dep.name, e.target.value)}
                            style={{ ...selectStyles, padding: '0.25rem 0.5rem' }}
                          >
                            {Object.entries(dep.install_commands).map(([pm, _]) => (
                              packageManagers[pm]?.available && (
                                <option key={pm} value={pm}>{pm}</option>
                              )
                            ))}
                          </select>
                        )}
                        <span style={{ color: '#f59e0b' }}>⚠ Optional</span>
                        {dep.error && <span style={{ fontSize: '0.75rem', color: '#f59e0b' }}> ({dep.error})</span>}
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Package managers */}
        <div style={{ marginBottom: '1.5rem' }}>
          <h4 style={{ fontSize: '0.875rem', fontWeight: '600', color: '#94a3b8', marginBottom: '0.75rem' }}>Package Managers Detected</h4>
          {Object.entries(packageManagers).length > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {Object.entries(packageManagers).map(([pm, data]) => (
                data.available && (
                  <div key={pm} style={{ fontSize: '0.75rem', color: '#6ee7b7' }}>✓ {data.description}</div>
                )
              ))}
            </div>
          ) : (
            <p style={{ fontSize: '0.75rem', color: '#94a3b8' }}>No package managers detected</p>
          )}
        </div>

        {/* Python package managers (if Python is installed) */}
        {Object.values(pythonPackageManagers).some(pm => pm.available) && (
          <div style={{ marginBottom: '1.5rem' }}>
            <h4 style={{ fontSize: '0.875rem', fontWeight: '600', color: '#94a3b8', marginBottom: '0.75rem' }}>Python Package Managers</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {Object.entries(pythonPackageManagers).map(([pm, data]) => (
                data.available && (
                  <div key={pm} style={{ fontSize: '0.75rem', color: '#6ee7b7' }}>✓ {data.description} ({data.version})</div>
                )
              ))}
            </div>
          </div>
        )}

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
          <button
            onClick={handleInstall}
            disabled={allRequiredInstalled || installing}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: allRequiredInstalled || installing ? '#475569' : '#3b82f6',
              color: 'white',
              border: 'none',
              borderRadius: '0.375rem',
              cursor: allRequiredInstalled || installing ? 'not-allowed' : 'pointer',
              fontWeight: '500',
            }}
          >
            {installing ? 'Installing...' : allRequiredInstalled ? 'All dependencies installed' : 'Install Selected'}
          </button>
          <button
            onClick={handleQuit}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: '#ef4444',
              color: 'white',
              border: 'none',
              borderRadius: '0.375rem',
              cursor: 'pointer',
              fontWeight: '500',
            }}
          >
            Refuse and Quit
          </button>
        </div>

        {/* Installation progress (auto-opening dropdown) */}
        {(installProgress.length > 0 || installing) && (
          <div style={{
            marginTop: '1rem',
            backgroundColor: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '0.375rem',
            overflow: 'hidden',
          }}>
            <button
              onClick={() => setInstallPanelOpen(!installPanelOpen)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.75rem 1rem',
                backgroundColor: '#1e293b',
                color: '#e2e8f0',
                border: 'none',
                cursor: 'pointer',
                fontSize: '0.875rem',
                fontWeight: '600',
              }}
            >
              <span>
                {installing ? '⏳ Installation en cours...' : '✓ Installation terminée'}
                {' '}({installProgress.filter(p => p.status === 'success').length}/{installProgress.length})
              </span>
              <span style={{ transition: 'transform 0.2s', transform: installPanelOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▾</span>
            </button>
            {installPanelOpen && (
              <div style={{ padding: '0 1rem 1rem' }}>
                {installProgress.map((p) => {
                  const badge = p.status === 'installing' ? { t: '⏳', c: '#fbbf24' }
                    : p.status === 'success' ? { t: '✓', c: '#6ee7b7' }
                    : p.status === 'failed' ? { t: '✗', c: '#f87171' }
                    : { t: '•', c: '#94a3b8' };
                  return (
                    <div key={p.name} style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.35rem 0',
                      borderBottom: '1px solid #334155',
                      fontSize: '0.8rem',
                    }}>
                      <span style={{ color: badge.c, fontWeight: '700', width: '1.2rem' }}>{badge.t}</span>
                      <span style={{ fontWeight: '500', minWidth: '6rem' }}>{p.name}</span>
                      <span style={{ color: '#94a3b8', fontSize: '0.7rem', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.detail}</span>
                    </div>
                  );
                })}
                {/* Raw log under the list */}
                {installLog.length > 0 && (
                  <div style={{
                    marginTop: '0.75rem',
                    backgroundColor: '#0f172a',
                    border: '1px solid #334155',
                    borderRadius: '0.375rem',
                    padding: '0.75rem',
                    maxHeight: '160px',
                    overflowY: 'auto',
                    fontFamily: 'monospace',
                    fontSize: '0.7rem',
                  }}>
                    {installLog.map((line, i) => (
                      <div key={i} style={{ color: line.includes('FAILED') || line.includes('ERROR') ? '#f87171' : line.includes('SUCCESS') ? '#6ee7b7' : '#94a3b8' }}>
                        {line}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Status */}
        {!checking && !error && (
          <div style={{ fontSize: '0.875rem', color: allRequiredInstalled ? '#6ee7b7' : '#fca5a5', marginTop: '1rem' }}>
            Status: {allRequiredInstalled ? '✓ All required dependencies are installed' : '✗ Missing required dependencies'}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;