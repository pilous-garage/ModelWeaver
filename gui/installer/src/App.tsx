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
                  {systemState.tools_installed.map(tool => (
                    <div key={tool.tool_ref} className="flex items-center justify-between text-sm bg-slate-900 px-2 py-1 rounded">
                      <span className="text-slate-300">{tool.tool_name}</span>
                      <span className="text-xs text-slate-500">{tool.version}</span>
                    </div>
                  ))}
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
          {catalogue ? (
            <div className="space-y-4 overflow-y-auto max-h-[600px] pr-2">
              {catalogue.classes.sort((a: any, b: any) => a.sort_order - b.sort_order).map(cls => {
                const toolsInClass = catalogue.catalog.filter((t: any) => t.class === cls.ref);
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
                          <div key={tool.ref} className="flex items-center justify-between p-3 bg-slate-900 rounded-lg border border-slate-700 hover:border-slate-500 transition">
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
                                  onClick={() => installTool(tool.ref)}
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