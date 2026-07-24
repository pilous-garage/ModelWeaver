import { useState, useEffect } from 'react';
import { useApp } from './useApp.ts';
import { getWindowLabel } from './bridge.ts';
import { DashboardPanel } from './panels/DashboardPanel.tsx';
import { SystemDashboardPanel } from './panels/SystemDashboardPanel.tsx';
import { DependenciesPanel } from './panels/DependenciesPanel.tsx';
import { AgentSandboxIDE } from './components/AgentSandboxIDE.tsx';

export default function App() {
  const app = useApp();
  const [windowLabel, setWindowLabel] = useState<string>('main');

  useEffect(() => {
    if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('sandbox') !== null) {
      setWindowLabel('sandbox');
      return;
    }
    getWindowLabel().then(setWindowLabel);
  }, []);

  if (windowLabel === 'sandbox') {
    return <AgentSandboxIDE />;
  }

  if (windowLabel === 'dashboard') {
    return <SystemDashboardPanel app={app} />;
  }

  return app.showDashboard ? <DashboardPanel app={app} /> : <DependenciesPanel app={app} />;
}
