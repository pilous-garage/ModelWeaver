import { useState, useEffect } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { useApp } from './useApp.ts';
import { DashboardPanel } from './panels/DashboardPanel.tsx';
import { DependenciesPanel } from './panels/DependenciesPanel.tsx';
import { AgentSandboxIDE } from './components/AgentSandboxIDE.tsx';

export default function App() {
  const app = useApp();
  const [windowLabel, setWindowLabel] = useState<string>('main');

  useEffect(() => {
    const w = getCurrentWindow();
    setWindowLabel(w.label);
  }, []);

  if (windowLabel === 'sandbox') {
    return <AgentSandboxIDE />;
  }

  return app.showDashboard ? <DashboardPanel app={app} /> : <DependenciesPanel app={app} />;
}
