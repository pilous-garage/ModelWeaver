import React, { useState, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/core';

const SCRIPTS_DIR = 'gui/installer/scripts';

interface HardwareInfo {
  ram_total_gb: number;
  ram_available_gb: number;
  disk_total_gb: number;
  disk_free_gb: number;
}

interface DependencieState {
  name: string;
  present: boolean;
  path: string | null;
}

interface InstalledTool {
  tool_ref: string;
  tool_name: string;
  tool_type: string;
  version: string;
  install_path: string;
  status: string;
}

interface SystemInfo {
  system: any;
  hardware: HardwareInfo;
  dependencies: DependencieState[];
  tools_installed: InstalledTool[];
}

interface Tool {
  ref: string;
  name: string;
  description: string;
  tool_type: string;
  current_version: string | null;
  class: string;
}

interface LocalTool {
  tool_ref: string;
  name: string;
  status: string;
  version: string | null;
  install_path: string | null;
}

interface ToolClass {
  ref: string;
  label: string;
  sort_order: number;
}

interface CatalogueData {
  catalog: Tool[];
  installed: LocalTool[];
  classes: ToolClass[];
}

function CollapsibleSection({ title, count, defaultOpen = true, children }: { title: string; count?: number | string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center justify-between w-full text-xs uppercase text-slate-500 font-bold mb-2 hover:text-slate-300 transition"
      >
        <span>{title}{count !== undefined ? ` (${count})` : ''}</span>
        <span className="text-slate-600">{open ? '▼' : '▶'}</span>
      </button>
      {open && children}
    </div>
  );
}

function App() {
  const [systemState, setSystemState] = useState<SystemInfo | null>(null);
  const [catalogue, setCatalogue] = useState<CatalogueData | null>(null);
  const [loading, setLoading] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [classFilter, setClassFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'installed' | 'not_installed'>('all');
  const [selectedTool, setSelectedTool] = useState<Tool | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  const addLog = (msg: string) => {
    setLogs(prev => [msg, ...prev].slice(0, 50));
  };

  const runCheck = async () => {
    setLoading(true);
    try {
      const res: any = await invoke('run_python_script', {
        scriptPath: `${SCRIPTS_DIR}/check.py`,
        args: []
      });
      if (res.status === 'success') {
        setSystemState(res.data);
        addLog('✅ System check completed');
      } else {
        addLog(`❌ Check error: ${res.error}`);
      }
    } catch (e) {
      addLog(`❌ System error: ${e}`);
    }
    setLoading(false);
  };

  const fetchCatalogue = async () => {
    setLoading(true);
    try {
      const res: any = await invoke('run_python_script', {
        scriptPath: `${SCRIPTS_DIR}/catalogue.py`,
        args: []
      });
      if (res.status === 'success') {
        setCatalogue(res.data);
        addLog('✅ Catalogue loaded');
      } else {
        addLog(`❌ Catalogue error: ${res.error}`);
      }
    } catch (e) {
      addLog(`❌ System error: ${e}`);
    }
    setLoading(false);
  };

  const installTool = async (ref: string) => {
    setLoading(true);
    addLog(`📦 Installing ${ref}...`);
    try {
      const res: any = await invoke('run_python_script', {
        scriptPath: `${SCRIPTS_DIR}/install.py`,
        args: [ref]
      });
      if (res.status === 'success') {
        addLog(`✅ ${ref} installed`);
        await fetchCatalogue();
        await runCheck();
      } else {
        addLog(`❌ ${ref} failed: ${res.error}`);
      }
    } catch (e) {
      addLog(`❌ System error: ${e}`);
    }
    setLoading(false);
  };

  useEffect(() => {
    runCheck();
    fetchCatalogue();
  }, []);

  return (
    <div className="min-h-screen p-8 flex flex-col gap-8 max-w-6xl mx-auto">
      <header className="flex justify-between items-center border-b border-slate-700 pb-4">
        <h1 className="text-3xl font-bold text-white">ModelWeaver Installer</h1>
        <div className="flex gap-4">
          <button
            onClick={runCheck}
            disabled={loading}
            className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-md text-sm transition"
          >
            Refresh Check
          </button>
          <button
            onClick={fetchCatalogue}
            disabled={loading}
            className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-md text-sm transition"
          >
            Refresh Catalogue
          </button>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
        {/* System State */}
        <section className="col-span-1 bg-slate-800 p-6 rounded-xl border border-slate-700 shadow-xl">
          <h2 className="text-xl font-semibold mb-4 text-slate-300">System State</h2>
          {systemState ? (
            <div className="space-y-6">
              <CollapsibleSection title="Hardware" count={undefined}>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <span className="text-slate-400">RAM:</span>
                  <span className="text-right">{systemState.hardware.ram_total_gb} GB</span>
                  <span className="text-slate-400">Disk:</span>
                  <span className="text-right">{systemState.hardware.disk_free_gb} GB free</span>
                </div>
              </CollapsibleSection>

              <CollapsibleSection title="Dependencies" count={systemState.dependencies.length}>
                <div className="space-y-2">
                  {systemState.dependencies.map(dep => (
                    <div key={dep.name} className="flex items-center justify-between text-sm">
                      <span className="text-slate-300">{dep.name}</span>
                      <span className={dep.present ? 'text-green-400' : 'text-red-400'}>
                        {dep.present ? '✅' : '❌'}
                      </span>
                    </div>
                  ))}
                </div>
              </CollapsibleSection>

              <CollapsibleSection title="Installed Tools" count={systemState.tools_installed.length}>
                <div className="space-y-1">
                  {(() => {
                    const byClass: Record<string, InstalledTool[]> = {};
                    for (const tool of systemState.tools_installed) {
                      const catTool = catalogue?.catalog.find((c: any) => c.ref === tool.tool_ref);
                      const cls = catTool?.class || 'other';
                      if (!byClass[cls]) byClass[cls] = [];
                      byClass[cls].push(tool);
                    }
                    const classes = catalogue?.classes || [];
                    const sortedClassRefs = Object.keys(byClass).sort((a, b) => {
                      const ca = classes.find((c: any) => c.ref === a);
                      const cb = classes.find((c: any) => c.ref === b);
                      return (ca?.sort_order ?? 99) - (cb?.sort_order ?? 99);
                    });
                    return sortedClassRefs.map(clsRef => {
                      const clsLabel = classes.find((c: any) => c.ref === clsRef)?.label || clsRef;
                      const tools = byClass[clsRef];
                      return (
                        <div key={clsRef} className="mb-2">
                          <div className="text-xs text-slate-500 uppercase mb-1">{clsLabel} ({tools.length})</div>
                          {tools.map(tool => (
                            <div key={tool.tool_ref} className="flex items-center justify-between text-sm bg-slate-900 px-2 py-1 rounded mb-0.5">
                              <span className="text-slate-300">{tool.tool_name}</span>
                              <span className="text-xs text-slate-500">{tool.version}</span>
                            </div>
                          ))}
                        </div>
                      );
                    });
                  })()}
                </div>
              </CollapsibleSection>
            </div>
          ) : (
            <div className="text-slate-500 italic text-center py-8">Checking system...</div>
          )}
        </section>

        {/* Catalogue by class */}
        <section className="col-span-2 bg-slate-800 p-6 rounded-xl border border-slate-700 shadow-xl">
          <h2 className="text-xl font-semibold mb-4 text-slate-300">Available Tools</h2>
          <input
            type="text"
            placeholder="Search tools..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="w-full mb-2 px-3 py-2 bg-slate-900 border border-slate-600 rounded-md text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition"
          />
          <div className="flex gap-2 mb-4">
            <select
              value={classFilter}
              onChange={e => setClassFilter(e.target.value)}
              className="px-2 py-1.5 bg-slate-900 border border-slate-600 rounded-md text-xs text-slate-300 focus:outline-none focus:border-blue-500"
            >
              <option value="">All classes</option>
              {catalogue && catalogue.classes.sort((a: any, b: any) => a.sort_order - b.sort_order).map(cls => (
                <option key={cls.ref} value={cls.ref}>{cls.label || cls.ref}</option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as any)}
              className="px-2 py-1.5 bg-slate-900 border border-slate-600 rounded-md text-xs text-slate-300 focus:outline-none focus:border-blue-500"
            >
              <option value="all">All</option>
              <option value="installed">Installed</option>
              <option value="not_installed">Not installed</option>
            </select>
          </div>
          {catalogue ? (
            <div className="space-y-4 overflow-y-auto max-h-[600px] pr-2">
              {catalogue.classes.sort((a: any, b: any) => a.sort_order - b.sort_order).map(cls => {
                if (classFilter && cls.ref !== classFilter) return null;
                let toolsInClass = catalogue.catalog.filter((t: any) => t.class === cls.ref);
                if (debouncedQuery) {
                  const q = debouncedQuery.toLowerCase();
                  toolsInClass = toolsInClass.filter((t: any) =>
                    t.name.toLowerCase().includes(q) ||
                    t.ref.toLowerCase().includes(q) ||
                    (t.description && t.description.toLowerCase().includes(q))
                  );
                }
                if (statusFilter === 'installed') {
                  toolsInClass = toolsInClass.filter((t: any) =>
                    catalogue.installed.some((it: any) => it.tool_ref === t.ref)
                  );
                } else if (statusFilter === 'not_installed') {
                  toolsInClass = toolsInClass.filter((t: any) =>
                    !catalogue.installed.some((it: any) => it.tool_ref === t.ref)
                  );
                }
                if (toolsInClass.length === 0) return null;
                const installedInClass = toolsInClass.filter((t: any) =>
                  catalogue.installed.some((it: any) => it.tool_ref === t.ref)
                );
                return (
                  <CollapsibleSection
                    key={cls.ref}
                    title={cls.label || cls.ref}
                    count={`${installedInClass.length}/${toolsInClass.length}`}
                  >
                    <div className="space-y-2">
                      {toolsInClass.map(tool => {
                        const isInstalled = catalogue.installed.some((it: any) => it.tool_ref === tool.ref);
                        const localTool = catalogue.installed.find((it: any) => it.tool_ref === tool.ref);
                        return (
                          <div
                            key={tool.ref}
                            onClick={() => setSelectedTool(tool)}
                            className="flex items-center justify-between p-3 bg-slate-900 rounded-lg border border-slate-700 hover:border-slate-500 transition cursor-pointer"
                          >
                            <div className="flex flex-col gap-0.5 flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-white truncate">{tool.name}</span>
                                <span className="text-xs text-slate-500 shrink-0">({tool.ref})</span>
                                {isInstalled && localTool && (
                                  <span className="text-xs text-green-400 shrink-0">v{localTool.version}</span>
                                )}
                              </div>
                              <span className="text-xs text-slate-400 truncate">{tool.description || 'No description'}</span>
                              <span className="text-xs text-slate-600">{tool.tool_type}</span>
                            </div>
                            <div className="flex items-center gap-4 shrink-0 ml-4">
                              {isInstalled ? (
                                <span className="text-xs bg-green-900 text-green-300 px-3 py-1.5 rounded-md font-medium border border-green-700">
                                  ✓ Installed
                                </span>
                              ) : (
                                <button
                                  onClick={(e) => { e.stopPropagation(); installTool(tool.ref); }}
                                  disabled={loading}
                                  className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded-md transition disabled:opacity-50 font-medium"
                                >
                                  Install
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </CollapsibleSection>
                );
              })}
            </div>
          ) : (
            <div className="text-slate-500 italic text-center py-8">Loading catalogue...</div>
          )}
        </section>
      </div>

      {/* Tool Detail Modal */}
      {selectedTool && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center" onClick={() => setSelectedTool(null)}>
          <div className="bg-slate-800 p-6 rounded-xl border border-slate-600 shadow-2xl max-w-lg w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className="text-xl font-bold text-white">{selectedTool.name}</h3>
                <span className="text-xs text-slate-500">({selectedTool.ref})</span>
              </div>
              <button onClick={() => setSelectedTool(null)} className="text-slate-400 hover:text-white text-xl leading-none">&times;</button>
            </div>
            <div className="space-y-3 text-sm text-slate-300">
              <div>
                <span className="text-slate-500 block text-xs">Description</span>
                <p>{selectedTool.description || 'No description'}</p>
              </div>
              <div className="flex gap-4">
                <div>
                  <span className="text-slate-500 block text-xs">Type</span>
                  <span className="bg-slate-700 px-2 py-0.5 rounded text-xs">{selectedTool.tool_type}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">Class</span>
                  <span className="bg-slate-700 px-2 py-0.5 rounded text-xs">{catalogue?.classes.find(c => c.ref === selectedTool.class)?.label || selectedTool.class}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">Status</span>
                  {catalogue?.installed.some(it => it.tool_ref === selectedTool.ref) ? (
                    <span className="text-green-400 text-xs">Installed</span>
                  ) : (
                    <span className="text-yellow-400 text-xs">Not installed</span>
                  )}
                </div>
              </div>
              {(() => {
                const localTool = catalogue?.installed.find(it => it.tool_ref === selectedTool.ref);
                return localTool ? (
                  <div>
                    <span className="text-slate-500 block text-xs">Install Info</span>
                    <p className="text-xs">Version: {localTool.version} | Path: {localTool.install_path}</p>
                  </div>
                ) : null;
              })()}
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <button onClick={() => setSelectedTool(null)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-md text-sm transition">Close</button>
              {!catalogue?.installed.some(it => it.tool_ref === selectedTool.ref) && (
                <button onClick={() => { installTool(selectedTool.ref); setSelectedTool(null); }} disabled={loading} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-md text-sm transition disabled:opacity-50">
                  Install
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Log Terminal */}
      <footer className="mt-auto">
        <div className="bg-black p-4 rounded-lg border border-slate-700 font-mono text-xs h-40 overflow-y-auto shadow-inner">
          <div className="text-slate-500 mb-2 border-b border-slate-800 pb-1">System Log</div>
          {logs.length === 0 && <div className="text-slate-700 italic">No activity...</div>}
          {logs.map((log, i) => (
            <div key={i} className="py-1 border-b border-slate-900 last:border-0">
              <span className="text-slate-600 mr-2">[{new Date().toLocaleTimeString()}]</span>
              <span className="text-slate-300">{log}</span>
            </div>
          ))}
        </div>
      </footer>
    </div>
  );
}

export default App;