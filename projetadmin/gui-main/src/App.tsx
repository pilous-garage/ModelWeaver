import { useApp } from './useApp.ts';
import { DashboardPanel } from './panels/DashboardPanel.tsx';
import { DependenciesPanel } from './panels/DependenciesPanel.tsx';

export default function App() {
  const app = useApp();
  return app.showDashboard ? <DashboardPanel app={app} /> : <DependenciesPanel app={app} />;
}
