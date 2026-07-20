import { useState, useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';
import type { Dependency, PackageManager, PythonPackageManager } from './types.ts';

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
        const data = await invoke<any>('daemon_post', { route: 'db/versions', body: '{}' });
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

export interface PanelGroupData {
  id: string;
  tabs: string[];
  activeTab: string;
}

export interface PanelGroup {
  id: string;
  tabs: string[];
  activeTab: string;
}

export interface PanelSplit {
  direction: 'horizontal' | 'vertical';
  children: PanelNode[];
}

export type PanelNode = PanelSplit | PanelGroup;

let groupIdCounter = 0;
function genGroupId(): string {
  return `pg-${++groupIdCounter}-${Date.now()}`;
}

export function useApp() {
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
  const [autoInstallTimer, setAutoInstallTimer] = useState<ReturnType<typeof setTimeout> | null>(null);
  const [installLog, setInstallLog] = useState<string[]>([]);
  const [installProgress, setInstallProgress] = useState<{ name: string; status: 'pending' | 'installing' | 'success' | 'failed'; detail: string }[]>([]);
  const [installPanelOpen, setInstallPanelOpen] = useState(false);

  // Logithèque state
  const [systemState, setSystemState] = useState<any>(null);
  const [catalogueTools, setCatalogueTools] = useState<any[]>([]);
  const [installedTools, setInstalledTools] = useState<any[]>([]);
  const [foldedClasses, setFoldedClasses] = useState<Record<string, boolean>>({});
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


  // Auto-test opt-in (piloté par MODELWEAVER_ENABLE_AUTOTEST côté Rust).
  const [autotestEnabled, setAutotestEnabled] = useState(false);

  // Debug / process manager panel
  const [showDebug, setShowDebug] = useState(false);
  const [showKeys, setShowKeys] = useState(false);
  const [keysList, setKeysList] = useState<any[]>([]);
  const [keysNewProvider, setKeysNewProvider] = useState('');
  const [keysNewValue, setKeysNewValue] = useState('');
  const [keysNewTag, setKeysNewTag] = useState<'free' | 'paid'>('free');
  const [providersList, setProvidersList] = useState<any[]>([]);
  const [modelsList, setModelsList] = useState<any[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [keyMsg, setKeyMsg] = useState<string>('');
  const [keyProviderMode, setKeyProviderMode] = useState<'known' | 'new'>('known');
  const [newProviderForm, setNewProviderForm] = useState<{ ref: string; name: string; provider_type: string; api_type: string; website: string }>({ ref: '', name: '', provider_type: 'cloud', api_type: '', website: '' });
  const [debugTab, setDebugTab] = useState<'process' | 'services' | 'logs' | 'resources'>('process');
  const [procList, setProcList] = useState<{ id: number; name: string; pid: number | null; parent_id: number | null; status: string; command: string; log_path: string; cpu: number; rss_kb: number; started_at: number; ended_at: number | null }[]>([]);
  const [procLogId, setProcLogId] = useState<number | null>(null);
  const [procLogText, setProcLogText] = useState<string>('');
  const [svcLogName, setSvcLogName] = useState<string | null>(null);
  const [svcLogText, setSvcLogText] = useState<string>('');
  const [serviceList, setServiceList] = useState<{ name: string; mode: string; status: string; pid: number | null; restarts: number; last_exit: number | null; started_at: number }[]>([]);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Chat
  const [showChat, setShowChat] = useState(false);
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatSending, setChatSending] = useState(false);
  const [chatProvider, setChatProvider] = useState('');
  const [chatModel, setChatModel] = useState('');

  // LLM locaux
  const [showLocal, setShowLocal] = useState(false);
  const [localEngines, setLocalEngines] = useState<any[]>([]);
  const [localLoading, setLocalLoading] = useState(false);
  const [localBusy, setLocalBusy] = useState<string>('');
  const [localMsg, setLocalMsg] = useState('');

  // Agents (Phase 4 : signaux + streaming)
  const [showAgents, setShowAgents] = useState(false);
  const [agentList, setAgentList] = useState<any[]>([]);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentMgr, setAgentMgr] = useState<{ active_agents: number; zombies: number[] }>({ active_agents: 0, zombies: [] });
  const [agentStreamText, setAgentStreamText] = useState('');
  const [agentStreamAgent, setAgentStreamAgent] = useState<number | null>(null);
  const [agentStreamSeq, setAgentStreamSeq] = useState(0);
  const [agentSignals, setAgentSignals] = useState<any[]>([]);

  // Panel management — tree-based layout (splits horizontaux/verticaux)
  function isSplit(node: PanelNode): node is PanelSplit {
    return 'direction' in node;
  }

  function cloneTree(node: PanelNode): PanelNode {
    if (isSplit(node)) {
      return { direction: node.direction, children: node.children.map(cloneTree) };
    }
    return { id: node.id, tabs: [...node.tabs], activeTab: node.activeTab };
  }

  function findGroupInTree(node: PanelNode, groupId: string): PanelGroup | null {
    if (!isSplit(node)) return node.id === groupId ? node : null;
    for (const c of node.children) {
      const found = findGroupInTree(c, groupId);
      if (found) return found;
    }
    return null;
  }

  function mapTreeGroups(node: PanelNode, fn: (g: PanelGroup) => PanelGroup): PanelNode {
    if (!isSplit(node)) return fn(node);
    return { direction: node.direction, children: node.children.map(c => mapTreeGroups(c, fn)) };
  }

  const [panelTree, setPanelTree] = useState<PanelNode>({
    direction: 'horizontal',
    children: [
      { id: genGroupId(), tabs: ['system-state', 'resources', 'installed-tools'], activeTab: 'system-state' },
      { id: genGroupId(), tabs: ['catalogue', 'chat', 'install-queue'], activeTab: 'catalogue' },
      { id: genGroupId(), tabs: ['agents', 'local-models', 'keys', 'debug'], activeTab: 'agents' },
    ],
  });

  const activateTab = (groupId: string, tabId: string) =>
    setPanelTree(prev => mapTreeGroups(prev, g => g.id === groupId ? { ...g, activeTab: tabId } : g));

  const closeTab = (groupId: string, tabId: string) =>
    setPanelTree(prev => {
      const tree = cloneTree(prev);
      const group = findGroupInTree(tree, groupId);
      if (!group || !isSplit(tree)) return prev;
      const idx = group.tabs.indexOf(tabId);
      if (idx === -1) return prev;
      group.tabs.splice(idx, 1);
      if (group.tabs.length > 0) {
        if (group.activeTab === tabId) group.activeTab = group.tabs[Math.min(idx, group.tabs.length - 1)];
        return tree;
      }
      return removeLeaf(tree, groupId);
    });

  function removeLeaf(node: PanelNode, leafId: string): PanelNode | null {
    if (!isSplit(node)) return node.id === leafId ? null : node;
    const filtered: PanelNode[] = [];
    for (const c of node.children) {
      const removed = removeLeaf(c, leafId);
      if (removed) filtered.push(removed);
    }
    if (filtered.length === 0) return null;
    if (filtered.length === 1) return filtered[0];
    return { direction: node.direction, children: filtered };
  }

  function findParentSplit(node: PanelNode, targetId: string): { parent: PanelSplit; idx: number } | null {
    if (!isSplit(node)) return null;
    for (let i = 0; i < node.children.length; i++) {
      const c = node.children[i];
      if (!isSplit(c) && c.id === targetId) return { parent: node, idx: i };
      const found = findParentSplit(c, targetId);
      if (found) return found;
    }
    return null;
  }

  const moveTabToGroup = (tabId: string, fromGroupId: string, toGroupId: string, insertIndex?: number) =>
    setPanelTree(prev => {
      const tree = cloneTree(prev);
      const fromG = findGroupInTree(tree, fromGroupId);
      const toG = findGroupInTree(tree, toGroupId);
      if (!fromG || !toG) return prev;
      const ti = fromG.tabs.indexOf(tabId);
      if (ti === -1) return prev;
      fromG.tabs.splice(ti, 1);
      if (fromG.activeTab === tabId) fromG.activeTab = fromG.tabs[Math.min(ti, fromG.tabs.length - 1)] || '';
      if (fromG.tabs.length === 0) {
        const afterRemove = removeLeaf(tree, fromGroupId);
        if (afterRemove) {
          const toG2 = findGroupInTree(afterRemove, toGroupId);
          if (toG2) {
            if (insertIndex !== undefined) toG2.tabs.splice(insertIndex, 0, tabId);
            else toG2.tabs.push(tabId);
          }
          return afterRemove;
        }
      }
      if (insertIndex !== undefined) toG.tabs.splice(insertIndex, 0, tabId);
      else toG.tabs.push(tabId);
      return tree;
    });

  const addTabToNewGroup = (tabId: string, fromGroupId: string) =>
    setPanelTree(prev => {
      const tree = cloneTree(prev);
      const fromG = findGroupInTree(tree, fromGroupId);
      if (!fromG || !isSplit(tree)) return prev;
      const ti = fromG.tabs.indexOf(tabId);
      if (ti === -1) return prev;
      fromG.tabs.splice(ti, 1);
      fromG.activeTab = fromG.tabs[Math.min(ti, fromG.tabs.length - 1)] || '';
      const newGroup: PanelGroup = { id: genGroupId(), tabs: [tabId], activeTab: tabId };
      if (fromG.tabs.length === 0) {
        const parent = findParentSplit(tree, fromGroupId);
        if (parent) {
          parent.parent.children.splice(parent.idx + 1, 0, newGroup);
          const after = removeLeaf(tree, fromGroupId);
          return after ?? tree;
        }
      }
      tree.children.push(newGroup);
      return tree;
    });

  const splitLeafAt = (leafId: string, direction: 'horizontal' | 'vertical', newGroup: PanelGroup) =>
    setPanelTree(prev => {
      const tree = cloneTree(prev);
      const parent = findParentSplit(tree, leafId);
      if (!parent) return prev;
      const leaf = findGroupInTree(tree, leafId);
      if (!leaf) return prev;

      if (parent.parent.direction === direction) {
        parent.parent.children.splice(parent.idx + 1, 0, newGroup);
      } else {
        parent.parent.children[parent.idx] = {
          direction,
          children: [leaf, newGroup],
        };
      }
      return tree;
    });

  const splitLeafAtWithTab = (leafId: string, direction: 'horizontal' | 'vertical', tabId: string, fromGroupId: string) =>
    setPanelTree(prev => {
      const tree = cloneTree(prev);
      const fromG = findGroupInTree(tree, fromGroupId);
      const targetG = findGroupInTree(tree, leafId);
      if (!fromG || !targetG) return prev;
      const ti = fromG.tabs.indexOf(tabId);
      if (ti === -1) return prev;
      fromG.tabs.splice(ti, 1);
      fromG.activeTab = fromG.tabs[Math.min(ti, fromG.tabs.length - 1)] || '';
      const newGroup: PanelGroup = { id: genGroupId(), tabs: [tabId], activeTab: tabId };
      if (fromG.tabs.length === 0) {
        const afterRemove = removeLeaf(tree, fromGroupId);
        if (afterRemove) {
          const parent = findParentSplit(afterRemove, leafId);
          if (!parent) return prev;
          if (parent.parent.direction === direction) {
            parent.parent.children.splice(parent.idx + 1, 0, newGroup);
          } else {
            parent.parent.children[parent.idx] = {
              direction,
              children: [findGroupInTree(afterRemove, leafId)!, newGroup],
            };
          }
          return afterRemove;
        }
      }
      const parent = findParentSplit(tree, leafId);
      if (!parent) return prev;
      if (parent.parent.direction === direction) {
        parent.parent.children.splice(parent.idx + 1, 0, newGroup);
      } else {
        parent.parent.children[parent.idx] = {
          direction,
          children: [targetG, newGroup],
        };
      }
      return tree;
    });

  // Hidden panels — show/hide from menu
  const [hiddenPanels, setHiddenPanels] = useState<Record<string, boolean>>({});

  const togglePanelVisibility = (id: string) => {
    if (hiddenPanels[id]) {
      setHiddenPanels(prev => { const n = { ...prev }; delete n[id]; return n; });
      setPanelTree(prev => {
        const tree = cloneTree(prev);
        return addTabToDefaultGroup(tree, id);
      });
    } else {
      setHiddenPanels(prev => ({ ...prev, [id]: true }));
      setPanelTree(prev => removeTabFromTree(prev, id));
    }
  };

  function addTabToDefaultGroup(tree: PanelNode, tabId: string): PanelNode {
    const col = PANEL_COLUMN[tabId] || 1;
    if (!isSplit(tree)) return tree;
    const idx = col === 'left' ? 0 : col === 'right' ? tree.children.length - 1 : Math.floor(tree.children.length / 2);
    const child = tree.children[idx];
    if (!isSplit(child)) {
      child.tabs.push(tabId);
      return tree;
    }
    tree.children.push({ id: genGroupId(), tabs: [tabId], activeTab: tabId });
    return tree;
  }

  function removeTabFromTree(node: PanelNode, tabId: string): PanelNode | null {
    if (!isSplit(node)) {
      if (node.tabs.includes(tabId)) {
        node.tabs = node.tabs.filter(t => t !== tabId);
        if (node.tabs.length === 0) return null;
      }
      return node;
    }
    const filtered: PanelNode[] = [];
    for (const c of node.children) {
      const removed = removeTabFromTree(c, tabId);
      if (removed) filtered.push(removed);
    }
    if (filtered.length === 0) return null;
    if (filtered.length === 1) return filtered[0];
    return { direction: node.direction, children: filtered };
  }

  const saveLayout = async () => {
    const data = JSON.stringify({ panelTree, hiddenPanels }, null, 2);
    try {
      await invoke('daemon_post', { route: 'file/save', body: JSON.stringify({ path: 'panel-conf.json', content: data }) });
    } catch {
      const blob = new Blob([data], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'panel-conf.json'; a.click();
      URL.revokeObjectURL(url);
    }
  };

  const loadLayout = async () => {
    try {
      const res = await invoke<any>('daemon_post', { route: 'file/load', body: JSON.stringify({ path: 'panel-conf.json' }) });
      if (res?.ok && res.result) {
        const { panelTree: pt, hiddenPanels: hp } = JSON.parse(res.result);
        if (pt) setPanelTree(pt);
        if (hp) setHiddenPanels(hp);
      }
    } catch { /* pas de fichier */ }
  };

  const PANEL_COLUMN: Record<string, 'left' | 'center' | 'right'> = {
    'system-state': 'left',
    'resources': 'left',
    'installed-tools': 'left',
    'catalogue': 'center',
    'chat': 'center',
    'install-queue': 'center',
    'agents': 'right',
    'local-models': 'right',
    'keys': 'right',
    'debug': 'right',
  };

  const ALL_PANELS = ['system-state', 'resources', 'installed-tools', 'catalogue', 'chat', 'install-queue', 'agents', 'local-models', 'keys', 'debug'];

  const setDebug = (v: boolean) => {
    setShowDebug(v);
    getCurrentWindow().setSize(new LogicalSize(v ? 1380 : 1000, v ? 760 : 700)).catch(() => {});
  };

  const fetchKeys = async () => {
    try {
      const data = await invoke<any>('daemon_post', { route: 'keys/list', body: '{}' });
      if (data.ok) setKeysList(data.result.keys || []);
    } catch { /* daemon pas encore prêt */ }
  };

  const fetchProviders = async () => {
    try {
      const res = await invoke<any>('get_providers');
      const list = res?.providers || [];
      setProvidersList(list);
      // Auto-sélection du 1er provider pour éviter un retour silencieux du "+"
      if (!keysNewProvider && list.length > 0) setKeysNewProvider(list[0].ref);
    }
    catch { /* ignore */ }
  };

  // Fetch des modèles : uniquement pour les providers ayant une clé API
  // (le daemon filtre côté backend via op_llm_models_list).
  const fetchModels = async () => {
    setModelsLoading(true);
    try {
      const data = await invoke<any>('daemon_post', { route: 'llm/models/list', body: '{}' });
      if (data && data.ok) setModelsList(data.result.models || []);
      else setModelsList([]);
    } catch { setModelsList([]); }
    finally { setModelsLoading(false); }
  };

  const fetchLocalEngines = async () => {
    setLocalLoading(true);
    setLocalMsg('');
    try {
      const data = await invoke<any>('daemon_post', { route: 'llm/local/list', body: '{}' });
      if (data && data.ok) setLocalEngines(data.result.engines || []);
      else setLocalEngines([]);
    } catch (e: any) { setLocalEngines([]); setLocalMsg(`Erreur: ${e}`); }
    finally { setLocalLoading(false); }
  };

  const handleLocalToggle = async (engine: string, running: boolean) => {
    if (localBusy) return;
    const action = running ? 'stop' : 'start';
    setLocalBusy(engine);
    setLocalMsg('');
    try {
      const data = await invoke<any>('daemon_post', {
        route: `llm/local/${action}`,
        body: JSON.stringify({ engine }),
      });
      if (data?.status !== 'ok') setLocalMsg(`⚠️ ${data?.error || 'échec'}`);
      await fetchLocalEngines();
    } catch (e: any) { setLocalMsg(`⚠️ ${e}`); }
    finally { setLocalBusy(''); }
  };

  // ── Agents (Phase 4) ──
  const fetchAgents = async () => {
    setAgentLoading(true);
    try {
      const data = await invoke<any>('daemon_post', { route: 'agent/list', body: '{}' });
      if (data?.ok || data?.agents) setAgentList(data.agents || []);
      try {
        const m = await invoke<any>('daemon_post', { route: 'agent/manager/status', body: '{}' });
        if (m) setAgentMgr({ active_agents: m.active_agents || 0, zombies: m.zombies || [] });
      } catch { /* ignore */ }
    } catch { /* ignore */ }
    finally { setAgentLoading(false); }
  };

  const sendAgentSignal = async (agentId: number, type: string, payload?: any) => {
    try {
      await invoke<any>('daemon_post', {
        route: 'agent/signal',
        body: JSON.stringify({ agent_id: agentId, type, payload }),
      });
      await fetchAgentSignals(agentId);
    } catch (e: any) { /* ignore */ }
  };

  const fetchAgentSignals = async (agentId: number) => {
    try {
      const data = await invoke<any>('daemon_post', {
        route: 'agent/signals', body: JSON.stringify({ agent_id: agentId }),
      });
      setAgentSignals(data?.signals || []);
    } catch { setAgentSignals([]); }
  };

  const watchAgentStream = async (agentId: number) => {
    setAgentStreamAgent(agentId);
    setAgentStreamText('');
    setAgentStreamSeq(0);
    await fetchAgentSignals(agentId);
  };

  const stopAgentStream = () => {
    setAgentStreamAgent(null);
    setAgentStreamText('');
  };

  const handleChatSend = async () => {
    const msg = chatInput.trim();
    if (!msg || !chatProvider || !chatModel || chatSending) return;
    const userMsg = { role: 'user', content: msg };
    const allMessages = [...chatMessages, userMsg];
    setChatMessages(allMessages);
    setChatInput('');
    setChatSending(true);
    // 1. Récupérer token/port pour fetch direct SSE
    let daemonToken = '';
    let daemonPort = 8770;
    try {
      const info = await invoke<any>('daemon_post', { route: 'auth/info', body: '{}' });
      if (info?.ok) {
        daemonToken = info.result.token;
        daemonPort = info.result.port;
      }
    } catch { /* fallback JSON */ }
    if (daemonToken) {
      // 2. SSE streaming
      let accumulated = '';
      const msgIdx = allMessages.length;
      setChatMessages([...allMessages, { role: 'assistant', content: '' }]);
      try {
        const response = await fetch(`http://127.0.0.1:${daemonPort}/v1/llm/chat/stream`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${daemonToken}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_ref: chatProvider, model_ref: chatModel, messages: allMessages }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const events = buffer.split('\n');
          buffer = events.pop() || '';
          for (const line of events) {
            if (line.startsWith('event: ')) continue; // event type line, skip
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.done) break;
                if (data.content !== undefined) {
                  accumulated += data.content;
                  setChatMessages(prev => {
                    const upd = [...prev];
                    upd[msgIdx] = { role: 'assistant', content: accumulated };
                    return upd;
                  });
                }
                if (data.error) throw new Error(data.error);
              } catch (e: any) { /* malformed JSON, ignore */ }
            }
          }
        }
      } catch (e: any) {
        setChatMessages(prev => {
          const upd = [...prev];
          upd[msgIdx] = { role: 'assistant', content: `⚠️ ${e?.message || String(e)}` };
          return upd;
        });
      }
    } else {
      // 3. Fallback JSON synchrone
      try {
        const data = await invoke<any>('daemon_post', {
          route: 'llm/chat',
          body: JSON.stringify({ provider_ref: chatProvider, model_ref: chatModel, messages: allMessages }),
        });
        const content = (data && data.status === 'ok')
          ? (data.content || '(réponse vide)')
          : `⚠️ ${data?.error || 'no response'}`;
        setChatMessages([...allMessages, { role: 'assistant', content }]);
      } catch (e: any) {
        setChatMessages([...allMessages, { role: 'assistant', content: `⚠️ Erreur: ${e?.toString?.() ?? String(e)}` }]);
      }
    }
    setChatSending(false);
  };

  const handleSetKey = async () => {
    if (!keysNewProvider || !keysNewValue) {
      setKeyMsg('⚠️ Sélectionnez un provider et saisissez une clé');
      return;
    }
    setKeyMsg('');
    try {
      const data = await invoke<any>('daemon_post', {
        route: 'keys/set',
        body: JSON.stringify({ provider_ref: keysNewProvider, api_key: keysNewValue, tag: keysNewTag })
      });
      if (!data || !data.ok) { setKeyMsg('⚠️ Échec: ' + (data?.error || 'inconnu')); return; }
      setKeysNewProvider(''); setKeysNewValue(''); setKeysNewTag('free');
      setKeyMsg('✅ Clé enregistrée pour ' + keysNewProvider);
      await fetchKeys();
    } catch (e: any) {
      setKeyMsg('⚠️ Erreur: ' + (e?.message || e));
    }
  };

  const handleAddProvider = async () => {
    const f = newProviderForm;
    if (!f.ref || !f.name) {
      setKeyMsg('⚠️ Ref et Name du nouveau provider requis');
      return;
    }
    setKeyMsg('');
    try {
      const res = await invoke<any>('add_provider', {
        dataJson: JSON.stringify({ ref: f.ref, name: f.name, provider_type: f.provider_type, api_type: f.api_type, website: f.website })
      });
      if (res && res.status === 'ok') {
        await fetchProviders();
        setKeysNewProvider(f.ref);
        setNewProviderForm({ ref: '', name: '', provider_type: 'cloud', api_type: '', website: '' });
        // Now add key via existing handler
        await handleSetKey();
      } else {
        setKeyMsg('⚠️ Provider non créé : ' + (res?.error || 'inconnu'));
      }
    } catch (e: any) {
      setKeyMsg('⚠️ Erreur : ' + (e?.message || e));
    }
  };

  const handleDeleteKey = async (providerRef: string) => {
    try {
      await invoke<any>('daemon_post', { route: 'keys/delete', body: JSON.stringify({ provider_ref: providerRef }) });
      await fetchKeys();
    } catch { /* ignore */ }
  };

  const handleToggleLock = async (ref: string, locked: boolean) => {
    try {
      await invoke<any>('daemon_post', { route: 'keys/set_lock', body: JSON.stringify({ ref, locked: !locked }) });
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
  const fetchAppVersion = () => invoke<any>('version').then((v) => { if (v?.result?.version) setAppVersion(String(v.result.version)); }).catch(() => {});

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

  // Polling du stream temps réel d'un agent (Phase 4) : append les chunks.
  useEffect(() => {
    if (agentStreamAgent == null) return;
    const poll = async () => {
      try {
        const data = await invoke<any>('daemon_post', {
          route: 'agent/stream',
          body: JSON.stringify({ agent_id: agentStreamAgent, seq: agentStreamSeq }),
        });
        if (data?.chunks?.length) {
          const text = data.chunks.map((c: any) => c.chunk).join('');
          setAgentStreamText(prev => prev + text);
          setAgentStreamSeq(data.seq);
        }
        await fetchAgentSignals(agentStreamAgent);
      } catch { /* ignore */ }
    };
    const h = setInterval(poll, 400);
    poll();
    return () => clearInterval(h);
  }, [agentStreamAgent]);

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

  // Polling temps réel des ressources GLOBALES (CPU/RAM/Disque machine) :
  // get_system_state lit Checker.get_hardware_info() qui track le CPU global.
  useEffect(() => {
    if (!showDashboard) return;
    const poll = async () => {
      try {
        setSystemState(await invoke<any>('get_system_state'));
      } catch { /* daemon indisponible */ }
    };
    const h = setInterval(poll, 1500);
    poll();
    return () => clearInterval(h);
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

  const isInstalled = (ref: string) => installedRef.current.some(t => t.ref === ref);
  const toggleFold = (classe: string) =>
    setFoldedClasses(prev => ({ ...prev, [classe]: !prev[classe] }));
  const queueJob = (ref: string) => installQueue.filter(q => q.ref === ref).sort((a, b) => b.id - a.id)[0] || null;
  const isQueued = (ref: string) => { const j = queueJob(ref); return !!j && (j.status === 'queued' || j.status === 'running'); };
  const fmtGb = (v: any) => (v == null ? 'n/a' : `${v} Go`);

  return {
    requiredDeps, setRequiredDeps,
    recommendedDeps, setRecommendedDeps,
    packageManagers, setPackageManagers,
    pythonPackageManagers, setPythonPackageManagers,
    checking, setChecking,
    error, setError,
    showDashboard, setShowDashboard,
    os, setOs,
    selectedPms, setSelectedPms,
    selectedRecommended, setSelectedRecommended,
    installing, setInstalling,
    autoInstallTimer, setAutoInstallTimer,
    installLog, setInstallLog,
    installProgress, setInstallProgress,
    installPanelOpen, setInstallPanelOpen,
    systemState, setSystemState,
    catalogueTools, setCatalogueTools,
    installedTools, setInstalledTools,
    foldedClasses, setFoldedClasses,
    logithequeLoading, setLogithequeLoading,
    logithequeError, setLogithequeError,
    loadingActions, setLoadingActions,
    appVersion, setAppVersion,
    pendingInstalls, setPendingInstalls,
    installQueue, setInstallQueue,
    autotestEnabled, setAutotestEnabled,
    showDebug, setShowDebug,
    showKeys, setShowKeys,
    keysList, setKeysList,
    keysNewProvider, setKeysNewProvider,
    keysNewValue, setKeysNewValue,
    keysNewTag, setKeysNewTag,
    providersList, setProvidersList,
    modelsList, setModelsList,
    modelsLoading, setModelsLoading,
    keyMsg, setKeyMsg,
    keyProviderMode, setKeyProviderMode,
    newProviderForm, setNewProviderForm,
    debugTab, setDebugTab,
    procList, setProcList,
    procLogId, setProcLogId,
    procLogText, setProcLogText,
    svcLogName, setSvcLogName,
    svcLogText, setSvcLogText,
    serviceList, setServiceList,
    isFullscreen, setIsFullscreen,
    showChat, setShowChat,
    chatMessages, setChatMessages,
    chatInput, setChatInput,
    chatSending, setChatSending,
    chatProvider, setChatProvider,
    chatModel, setChatModel,
    showLocal, setShowLocal,
    localEngines, setLocalEngines,
    localLoading, setLocalLoading,
    localBusy, setLocalBusy,
    localMsg, setLocalMsg,
    showAgents, setShowAgents,
    agentList, setAgentList,
    agentLoading, setAgentLoading,
    agentMgr, setAgentMgr,
    agentStreamText, setAgentStreamText,
    agentStreamAgent, setAgentStreamAgent,
    agentStreamSeq, setAgentStreamSeq,
    agentSignals, setAgentSignals,
    panelTree, activateTab, closeTab, moveTabToGroup, addTabToNewGroup, splitLeafAt, splitLeafAtWithTab,
    hiddenPanels, togglePanelVisibility, saveLayout, loadLayout, ALL_PANELS,
    installListOpen, setInstallListOpen,
    installedRef,
    installQueueRef,
    prevStatusRef,
    autoInstalledRef,
    CATALOGUE_URL,
    requiredDepsRef,
    recommendedDepsRef,
    selectedPmsRef,
    lastDepCheckRef,
    selectedRecommendedRef,
    withFeedback,
    setDebug,
    fetchKeys,
    fetchProviders,
    fetchModels,
    fetchLocalEngines,
    handleLocalToggle,
    fetchAgents,
    sendAgentSignal,
    fetchAgentSignals,
    watchAgentStream,
    stopAgentStream,
    handleChatSend,
    handleSetKey,
    handleAddProvider,
    handleDeleteKey,
    handleToggleLock,
    toggleFullscreen,
    fetchProcList,
    fetchProcLog,
    fetchServiceLog,
    fetchServiceList,
    fetchAppVersion,
    addLog,
    checkDependencies,
    checkDependenciesThrottled,
    handleRecommendedToggle,
    handlePmChange,
    updateProgress,
    handleInstall,
    handleQuit,
    refreshInstalled,
    refreshSysState,
    loadLogitheque,
    handleUninstallTool,
    handleAddToInstallList,
    handleCancelInstall,
    handleClearQueue,
    allRequiredInstalled,
    isInstalled,
    toggleFold,
    queueJob,
    isQueued,
    fmtGb,
  };
}

export type AppApi = ReturnType<typeof useApp>;
