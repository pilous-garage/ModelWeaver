import React from 'react';
import { AgentsPanel } from '../../panels/AgentsPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "agents-liste", label: "Agents", icon: "smart_toy", version: "1.0.0",
  description: "Liste et contrôle des agents actifs",
  daemonRoutes: [
    { route: "agent/list", methods: ["GET"], desc: "Liste des agents" },
    { route: "agent/get", methods: ["GET"], desc: "Détail d'un agent" },
    { route: "agent/signal", methods: ["POST"], desc: "Pause/Resume/Kill" },
    { route: "agent/metrics", methods: ["GET"], desc: "Métriques" },
  ],
  menu: [
    { menuPath: ["Agents"], id: "agents:refresh", label: "Rafraîchir", action: "panel:agents:refresh" },
    { menuPath: ["Agents"], id: "agents:launch", label: "Lancer un agent…", action: "panel:agents:launch" },
    { menuPath: ["Agents"], id: "agents:kill-all", label: "Arrêter tout", action: "panel:agents:kill-all" },
  ],
  declaration: () => [
    "[agents-liste] Agents v1.0.0",
    "  Liste et contrôle des agents actifs",
    "  Routes: agent/list, agent/get, agent/signal, agent/metrics",
    "  Menu: Agents > Rafraîchir | Lancer… | Arrêter tout",
  ].join("\n"),
  component: ({ ctx }) => React.createElement(AgentsPanel, { app: ctx.api }),
};
