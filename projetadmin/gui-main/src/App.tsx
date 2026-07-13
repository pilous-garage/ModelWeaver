import React, { useState, useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';

interface Dependency {
  name: string;
  description: string;
  check_command?: string;
  version_regex?: string;
  min_version?: string;
  install_commands?: Record<string, string>;
  installed?: boolean;
  version?: string;
  error?: string;
  // Champs issu du manifeste de dépendances
  language?: string;
  safe?: boolean;
  weight?: string;
  optional?: boolean;
  required?: boolean;
  target_pkg?: string;
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

// Refresh paresseux : on poll /v1/db/versions (PRAGMA data_version par DB +
// meta 'dependencies') et on ne rafraîchit que les panneaux du domaine ayant
// changé. Pas de poll lourd : l'endpoint est trivial et le compare côté GUI.
type Domain = 'catalogue' | 'inventory' | 'runtime' | 'dependencies';
function useDomainVersions(enabled: boolean, onChange: (domains: Domain[]) => void) {
  const prevRef = useRef<Record<string, number>>({});
  const cbRef = useRef(onChange);
  cbRef.current = onChange;
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const poll = async () => {
      if (cancelled) return;
      try {
        const token = await invoke<string>('watch_get', { name: 'api_token' });
        if (!token) return;
        const res = await fetch('http://127.0.0.1:8770/v1/db/versions', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: '{}',
        });
        const data = await res.json();
        if (!data.ok) return;
        const v = (data.result || {}) as Record<string, number>;
        const prev = prevRef.current;
        const ch: Domain[] = [];
        for (const k of Object.keys(v) as Domain[]) {
          if (prev[k] !== undefined && prev[k] !== v[k]) ch.push(k);
        }
        prevRef.current = v;
        if (ch.length) cbRef.current(ch);
      } catch {
        /* daemon pas encore prêt */
      }
    };
    const h = setInterval(poll, 300);
    poll();
    return () => { cancelled = true; clearInterval(h); };
  }, [enabled]);
}

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
  const [loadingActions, setLoadingActions] = useState<Record<string, boolean>>({});
  const [appVersion, setAppVersion] = useState<string>('');
  const [pendingInstalls, setPendingInstalls] = useState<Record<string, boolean>>({});
  const [installQueue, setInstallQueue] = useState<{ id: number; ref: string; name: string; job_type: string; status: string; log: string }[]>([]);

  const withFeedback = async <T,>(actionName: string, action: () => Promise<T>): Promise<T | void> => {
    setLoadingActions(prev => ({ ...prev, [actionName]: true }));
    try {
      return await action();
    } finally {
      setLoadingActions(prev => ({ ...prev, [actionName]: false }));
    }
  };

  const Spinner = ({ size = 12, color = '#e2e8f0' }: { size?: number; color?: string }) => (
    <span
      style={{
        display: 'inline-block',
        width: size, height: size,
        border: `2px solid ${color}`,
        borderTopColor: 'transparent',
        borderRadius: '50%',
        animation: 'mw-spin 0.7s linear infinite',
        verticalAlign: 'middle',
      }}
    />
  );


  // Auto-test opt-in (piloté par MODELWEAVER_ENABLE_AUTOTEST côté Rust).
  const [autotestEnabled, setAutotestEnabled] = useState(false);

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
  const [svcLogName, setSvcLogName] = useState<string | null>(null);
  const [svcLogText, setSvcLogText] = useState<string>('');
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
  const fetchServiceLog = (name: string) => {
    setSvcLogName(name);
    invoke<string>('service_log', { name, lines: 200 }).then(setSvcLogText).catch(() => setSvcLogText(''));
  };
  const fetchServiceList = () => invoke<any[]>('service_list').then(setServiceList).catch(() => {});
  const fetchAppVersion = () => invoke<string>('app_version').then(setAppVersion).catch(() => {});

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
  // Throttle du check-manifest (lourd ~0.7s : subprocess pip show / dpkg-query).
  // On ne le lance qu'une fois au montage + toutes les 60s, jamais sur chaque tick.
  const lastDepCheckRef = useRef(0);
  const selectedRecommendedRef = useRef<Record<string, boolean>>({});

  const addLog = (msg: string) => {
    const ts = new Date().toISOString().split('T')[1]?.split('.')[0] || '';
    const line = `[${ts}] ${msg}`;
    setInstallLog(prev => [...prev, line]);
    invoke('log_message', { level: 'INSTALL', message: msg }).catch(() => {});
  };

  useEffect(() => {
    fetchAppVersion();
    checkDependencies();
    try {
      invoke<boolean>('autotest_enabled_cmd').then(setAutotestEnabled).catch(() => setAutotestEnabled(false));
    } catch { setAutotestEnabled(false); }
    return () => { if (autoInstallTimer) clearTimeout(autoInstallTimer); };
  }, []);

  // Dès qu'un outil apparaît dans installedTools, on retire son « added »
  // en attente : le bouton bascule alors sur « ✓ Installé ».
  useEffect(() => {
    if (Object.keys(pendingInstalls).length === 0) return;
    setPendingInstalls(p => {
      let changed = false;
      const next: Record<string, boolean> = {};
      for (const k of Object.keys(p)) {
        if (!installedTools.some(t => t.ref === k)) next[k] = true;
        else changed = true;
      }
      return changed ? next : p;
    });
  }, [installedTools]);

  // Refresh paresseux piloté par les data_version des DB (split physique).
  // On ne rafraîchit que les panneaux du/des domaine(s) ayant changé.
  useDomainVersions(true, async (domains: Domain[]) => {
    const d = new Set(domains);
    try {
      if (d.has('inventory')) {
        await refreshInstalled();
        await refreshSysState();
        await fetchKeys();
      }
      if (d.has('catalogue')) {
        const cat = await invoke<any>('get_catalogue_tools');
        setCatalogueTools(cat.tools || []);
        await refreshInstalled();
      }
      if (d.has('runtime')) {
        const status = await invoke<any[]>('install_queue_status');
        setInstallQueue(status);
        installQueueRef.current = status;
        await fetchProcList();
        await fetchServiceList();
      }
      if (d.has('dependencies')) {
        await checkDependenciesThrottled();
      }
    } catch {
      /* daemon indisponible : on ignore */
    }
  });

  useEffect(() => {
    if (showDashboard) {
      loadLogitheque();
      // Premier check-manifest au montage (force), puis périodique 60s.
      lastDepCheckRef.current = 0;
      checkDependencies();
      const depTimer = setInterval(() => checkDependenciesThrottled(), 60000);
      return () => {
        installQueueRef.current = [];
        clearInterval(depTimer);
      };
    }
    installQueueRef.current = [];
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
      // Dépendances : depuis le manifeste (paquet dispo par cible).
      // daemon_post renvoie { ok, route, result: { target, dependencies } }.
      const resp = await invoke<{
        result: { target: string; dependencies: Dependency[] };
      }>('check_dependencies_manifest');
      const manifest = resp.result;
      setOs(manifest.target);

      // Requis = safe + light + non-optionnelles ; le reste = optionnelles.
      const requiredDepsList: Dependency[] = manifest.dependencies.filter((d: any) => d.required);
      const recommendedDepsList: Dependency[] = manifest.dependencies.filter((d: any) => !d.required);
      setRequiredDeps(requiredDepsList);
      requiredDepsRef.current = requiredDepsList;
      setRecommendedDeps(recommendedDepsList);
      recommendedDepsRef.current = recommendedDepsList;

      // Optionnelles pré-cochées = light & safe (ex: git) ; pas les heavy/unsafe
      // (litellm, docker) qui restent un choix explicite de l'utilisateur.
      const initialSelectedRecommended: Record<string, boolean> = {};
      recommendedDepsList.forEach((dep: any) => {
        initialSelectedRecommended[dep.name] = !(dep.weight === 'heavy' || dep.safe === false);
      });
      setSelectedRecommended(initialSelectedRecommended);
      selectedRecommendedRef.current = initialSelectedRecommended;
      

      // Show dashboard if all required dependencies are installed
      if (requiredDepsList.every(dep => dep.installed)) {
        setShowDashboard(true);
      } else if (autotestEnabled) {
        addLog(`Missing dependencies detected: ${requiredDepsList.filter(d => !d.installed).map(d => d.name).join(', ')}`);
        addLog('Auto-install will start in 10 seconds...');
        const timer = setTimeout(() => {
          addLog('Auto-install triggered');
          handleInstall();
        }, 10000);
        setAutoInstallTimer(timer);
      } else {
        addLog(`Missing dependencies detected: ${requiredDepsList.filter(d => !d.installed).map(d => d.name).join(', ')}`);
        addLog('Auto-install désactivé (MODELWEAVER_ENABLE_AUTOTEST non défini)');
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

  // Check-manifest throttlé : le check complet est lourd (~0.7s, subprocess pip/dpkg).
  // On le force au montage et ensuite au plus toutes les 60s (jamais sur chaque tick
  // du poll db/versions). Un install (handleInstall / install_dependency) le relance
  // explicitement, donc l'UI reste à jour sans surcharge.
  const checkDependenciesThrottled = async () => {
    const now = Date.now();
    if (now - lastDepCheckRef.current < 60000) return;
    lastDepCheckRef.current = now;
    await checkDependencies();
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
    addLog('=== Starting installation (target script) ===');

    const reqs = requiredDepsRef.current;
    const recs = recommendedDepsRef.current;
    const selRec = selectedRecommendedRef.current;

    // Plan d'affichage : deps requises manquantes + recommandées cochées.
    const plan: { dep: Dependency; required: boolean }[] = [];
    for (const dep of reqs) {
      if (!dep.installed) plan.push({ dep, required: true });
    }
    for (const dep of recs) {
      if (!dep.installed && selRec[dep.name]) plan.push({ dep, required: false });
    }
    // Si une optionnelle (heavy/unsafe) est cochée -> le script cible installe
    // aussi les deps optionnelles (--include-optional).
    const includeOptional = plan.some(p => !p.required);

    try {
      addLog(`Installing ${plan.length} dependencies via manifest target script...`);
      // Marque tout le plan en 'installing' (le script cible installe en une passe).
      for (const { dep } of plan) updateProgress(dep.name, 'installing', 'via target script...');

      try {
        const result = await invoke<string>('install_all_dependencies', { includeOptional });
        addLog(`  target install: SUCCESS (${result})`);
      } catch (err: any) {
        addLog(`  target install: FAILED - ${err}`);
        for (const { dep } of plan) updateProgress(dep.name, 'failed', String(err).substring(0, 200));
        throw err;
      }

      // Re-vérifie : les deps requises OK passent en success.
      await checkDependencies();
      for (const { dep } of plan) {
        if (dep.installed) updateProgress(dep.name, 'success', 'OK');
      }
      addLog('=== Installation complete, re-checking dependencies ===');
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
      // Auto-install tous les outils non installés (une seule fois au premier
      // chargement) — uniquement si MODELWEAVER_ENABLE_AUTOTEST est actif.
      if (autotestEnabled && !autoInstalledRef.current) {
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
    withFeedback(`uninstall-${ref}`, async () => {
      try {
        const id = await invoke('install_queue_add', { ref, name, jobType: 'uninstall' });
        if (!id) addLog(`${name} déjà en cours ou en file`);
      } catch (e: any) {
        addLog(`  ${name}: ERREUR file ${e}`);
      }
    });
  };

  const handleAddToInstallList = (ref: string, name: string) => {
    withFeedback(`install-${ref}`, async () => {
      // Guard basé sur pendingInstalls (mémoire UI immédiate) et NON sur
      // installQueue (state potentiellement stale juste après un cancel, avant
      // le prochain poll ~1s) — sinon un cancel suivi d'un re-clic est bloqué
      // par un faux "déjà dans la file".
      const deja = pendingInstalls[ref] === true;
      if (deja) {
        addLog(`${name} déjà dans la file`);
        return;
      }
      addLog(`Ajout de ${name} à la file d'installation (séquentielle, thread dédié)`);
      try {
        const id = await invoke<number>('install_queue_add', { ref, name, jobType: 'install' });
        if (!id) addLog(`${name} déjà en cours ou en file`);
        else setPendingInstalls(p => ({ ...p, [ref]: true }));
      } catch (e: any) {
        addLog(`  ${name}: ERREUR file ${e}`);
      }
    });
  };

  const handleCancelInstall = (id: number, name: string, ref?: string) => {
    withFeedback(`cancel-${id}`, async () => {
      if (ref) setPendingInstalls(p => { const n = { ...p }; delete n[ref]; return n; });
      addLog(`Annulation de ${name} (job #${id})`);
      await invoke('install_queue_cancel', { id }).catch((e: any) => addLog(`  cancel ERREUR ${e}`));
    });
  };

  const handleClearQueue = () => {
    withFeedback(`clear-queue`, async () => {
      await invoke('install_queue_clear').catch(() => {});
    });
  };

  const allRequiredInstalled = requiredDeps.every(dep => dep.installed);

  if (showDashboard) {
    const isInstalled = (ref: string) => installedRef.current.some(t => t.ref === ref);
    const queueJob = (ref: string) => installQueue.filter(q => q.ref === ref).sort((a, b) => b.id - a.id)[0] || null;
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
        <style>{`@keyframes mw-spin { to { transform: rotate(360deg); } }`}</style>
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
            <div style={{ fontSize: '0.65rem', color: '#475569', marginTop: '0.1rem', fontFamily: 'monospace' }}>
              {appVersion ? `v${appVersion}` : 'v…'}
            </div>
          </div>
          <button
            onClick={() => withFeedback('load-logitheque', async () => { setCatalogueTools([]); await refreshInstalled(); await loadLogitheque(); })}
            disabled={loadingActions['load-logitheque']}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: loadingActions['load-logitheque'] ? '#1e293b' : '#334155', color: loadingActions['load-logitheque'] ? '#64748b' : '#e2e8f0', border: 'none', borderRadius: '0.375rem', cursor: loadingActions['load-logitheque'] ? 'default' : 'pointer', fontSize: '0.75rem', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
          >
            {loadingActions['load-logitheque'] ? <Spinner /> : '↻'} Rafraîchir
          </button>
          <button
            onClick={() => withFeedback('install-all', async () => {
              addLog('Installation automatique de tous les outils...');
              try { const r = await invoke<any>('install_all_tools'); addLog(`Résultat: ${JSON.stringify(r)}`); } catch (e) { addLog(`Erreur install all: ${e}`); }
            })}
            disabled={loadingActions['install-all']}
            style={{ padding: '0.4rem 0.8rem', backgroundColor: loadingActions['install-all'] ? '#064e3b' : '#059669', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: loadingActions['install-all'] ? 'default' : 'pointer', fontSize: '0.75rem', fontWeight: '600', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
          >
            {loadingActions['install-all'] ? <Spinner /> : '⚡'} Tout installer
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
                        disabled={loadingActions[`uninstall-${t.ref}`]}
                        style={{ backgroundColor: '#7f1d1d', color: loadingActions[`uninstall-${t.ref}`] ? '#fca5a5' : '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.25rem', padding: '0.1rem 0.5rem', fontSize: '0.7rem', cursor: loadingActions[`uninstall-${t.ref}`] ? 'default' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                      >
                        {loadingActions[`uninstall-${t.ref}`] ? <Spinner size={10} color="#fca5a5" /> : null} Uninstall
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
                            const added = loadingActions[`install-${t.ref}`] || pendingInstalls[t.ref] === true || (j != null && (j.status === 'queued' || j.status === 'running'));
                            if (inst || (j != null && (j.status === 'installed' || j.status === 'removed'))) {
                              const txt = j != null && j.status === 'removed' ? '✓ Retiré' : '✓ Installé';
                              return (
                                <span style={{ color: '#6ee7b7', fontSize: '0.75rem', fontWeight: '600' }}>{txt}</span>
                              );
                            }
                            if (added) {
                              return (
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
                                  <button
                                    disabled
                                    style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', padding: '0.4rem 0.7rem', backgroundColor: '#1e3a8a', color: '#bfdbfe', border: 'none', borderRadius: '0.375rem', cursor: 'default', fontSize: '0.72rem', fontWeight: '500', opacity: 0.9 }}
                                  ><Spinner size={10} color="#bfdbfe" />added</button>
                                  {j != null && (j.status === 'queued' || j.status === 'running') && (
                                    <button
                                      onClick={() => handleCancelInstall(j.id, t.name, t.ref)}
                                      disabled={loadingActions[`cancel-${j.id}`]}
                                      style={{ padding: '0.25rem 0.55rem', backgroundColor: loadingActions[`cancel-${j.id}`] ? '#4c0519' : '#7f1d1d', color: '#fecaca', border: '1px solid #b91c1c', borderRadius: '0.3rem', cursor: loadingActions[`cancel-${j.id}`] ? 'default' : 'pointer', fontSize: '0.7rem', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                                    >{loadingActions[`cancel-${j.id}`] ? <Spinner size={9} color="#fecaca" /> : null}Annuler</button>
                                  )}
                                </span>
                              );
                            }
                            return (
                              <button
                                onClick={() => handleAddToInstallList(t.ref, t.name)}
                                disabled={loadingActions[`install-${t.ref}`]}
                                style={{ padding: '0.4rem 0.7rem', backgroundColor: loadingActions[`install-${t.ref}`] ? '#1e3a8a' : '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', cursor: loadingActions[`install-${t.ref}`] ? 'default' : 'pointer', fontSize: '0.72rem', fontWeight: '500', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                              >
                                {loadingActions[`install-${t.ref}`] ? <Spinner size={10} color="#fff" /> : null}+ Add to install list
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
                                onClick={() => handleCancelInstall(q.id, q.name, q.ref)}
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
                        disabled={loadingActions['clear-queue']}
                        style={{ marginTop: '0.5rem', padding: '0.25rem 0.6rem', backgroundColor: loadingActions['clear-queue'] ? '#1e293b' : '#334155', color: loadingActions['clear-queue'] ? '#64748b' : '#cbd5e1', border: '1px solid #475569', borderRadius: '0.3rem', cursor: loadingActions['clear-queue'] ? 'default' : 'pointer', fontSize: '0.7rem', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                      >{loadingActions['clear-queue'] ? <Spinner size={9} color="#64748b" /> : null}Vider la file (terminés)</button>
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
                          <button onClick={() => fetchServiceLog(s.name)} style={{ marginLeft: '0.3rem', fontSize: '0.62rem', padding: '0.1rem 0.4rem', backgroundColor: '#334155', color: '#cbd5e1', border: '1px solid #475569', borderRadius: '0.25rem', cursor: 'pointer' }}>logs</button>
                        </div>
                        <div style={{ color: '#64748b', fontSize: '0.65rem', marginTop: '0.1rem' }}>
                          {s.mode}{s.last_exit != null ? ` · exit ${s.last_exit}` : ''} · démarré {new Date(s.started_at * 1000).toLocaleTimeString()}
                        </div>
                        {svcLogName === s.name && (
                          <pre style={{ marginTop: '0.3rem', maxHeight: '160px', overflowY: 'auto', backgroundColor: '#0f172a', color: '#a5b4fc', fontSize: '0.62rem', padding: '0.4rem', borderRadius: '0.25rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{svcLogText || '(vide)'}</pre>
                        )}
                      </div>
                    );
                  })
                    )}
                  </>
                )}
                {debugTab === 'logs' && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', height: '100%' }}>
                    <div style={{ fontSize: '0.66rem', color: '#64748b' }}>Logs d'install / dépendances</div>
                    {installLog.length === 0 ? (
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun log pour l'instant — les actions d'install généreront des entrées ici.</div>
                    ) : (
                      <div style={{ backgroundColor: '#0f172a', borderRadius: '0.375rem', border: '1px solid #334155', padding: '0.6rem', maxHeight: '100%', overflowY: 'auto', fontFamily: 'monospace', fontSize: '0.66rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                        {installLog.map((line, i) => (
                          <div key={i} style={{ color: line.includes('FAILED') || line.includes('ERROR') ? '#f87171' : line.includes('SUCCESS') ? '#6ee7b7' : '#94a3b8' }}>{line}</div>
                        ))}
                      </div>
                    )}
                  </div>
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
      overflowY: 'auto',
    }}>
      <style>{`@keyframes mw-spin { to { transform: rotate(360deg); } }`}</style>
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
                    <span style={{ color: '#6ee7b7' }}>✓ Installé</span>
                  ) : (
                    <span style={{ color: '#f59e0b' }} title={dep.target_pkg}>⚠ Sera installé</span>
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
                      <span style={{ color: '#6ee7b7' }}>✓ Installé</span>
                    ) : (
                      <span style={{ color: '#f59e0b' }} title={dep.target_pkg}>⚠ Optionnel</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
          <button
            onClick={() => withFeedback('install-deps', handleInstall)}
            disabled={allRequiredInstalled || installing || loadingActions['install-deps']}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: (allRequiredInstalled || installing || loadingActions['install-deps']) ? '#475569' : '#3b82f6',
              color: 'white',
              border: 'none',
              borderRadius: '0.375rem',
              cursor: (allRequiredInstalled || installing || loadingActions['install-deps']) ? 'not-allowed' : 'pointer',
              fontWeight: '500',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.4rem',
            }}
          >
            {(installing || loadingActions['install-deps']) && <Spinner />}
            {installing
              ? `Installing... (${installProgress.filter(p => p.status === 'success').length}/${installProgress.length})`
              : allRequiredInstalled ? 'All dependencies installed' : 'Install Selected'}
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
                {installing
                  ? `⏳ Installation... (${installProgress.filter(p => p.status === 'success').length}/${installProgress.length})`
                  : '✓ Installation terminée'}
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