import { useState, useEffect } from 'react';
import { useApp } from './useApp.ts';
import { getWindowLabel } from './bridge.ts';
import { DashboardPanel } from './panels/DashboardPanel.tsx';
import { DependenciesPanel } from './panels/DependenciesPanel.tsx';
import { AgentSandboxIDE } from './components/AgentSandboxIDE.tsx';

export default function App() {
  const app = useApp();
  const [windowLabel, setWindowLabel] = useState<string>('main');

  useEffect(() => {
    // En mode navigateur, ?sandbox force l'affichage du sandbox
    if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('sandbox') !== null) {
      setWindowLabel('sandbox');
      return;
    }
    getWindowLabel().then(setWindowLabel);
  }, []);

  if (windowLabel === 'sandbox') {
    return <AgentSandboxIDE />;
  }

  return app.showDashboard ? <DashboardPanel app={app} /> : <DependenciesPanel app={app} />;
}
