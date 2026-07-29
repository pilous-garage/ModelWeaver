import React from 'react';
import { AgentMonitoringPanel } from '../../panels/AgentMonitoringPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "agents-monitoring", label: "Monitoring", icon: "monitoring", version: "1.0.0",
  description: "Métriques temps réel des agents et services",
  daemonRoutes: [
    { route: "agent/metrics", methods: ["GET"], desc: "Métriques agents" },
    { route: "service/resources", methods: ["GET"], desc: "Ressources services" },
  ],
  menu: [{ menuPath: ["Affichage"], id: "monitoring:live", label: "Pause/Reprendre live", action: "panel:monitoring:toggle-live" }],
  declaration: () => "[agents-monitoring] Monitoring v1.0.0\n  Routes: agent/metrics, service/resources",
  component: ({ ctx }) => React.createElement(AgentMonitoringPanel),
};
