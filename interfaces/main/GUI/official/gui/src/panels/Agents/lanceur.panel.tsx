import React from 'react';
import AgentLauncherPanel from '../../panels/AgentLauncherPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "agents-lanceur", label: "Lanceur", icon: "play_arrow", version: "1.0.0",
  description: "Lancer un agent individuel avec une requête",
  daemonRoutes: [
    { route: "agent/launch", methods: ["POST"], desc: "Lancer un agent" },
  ],
  menu: [{ menuPath: ["Agents"], id: "launcher:open", label: "Lanceur rapide", action: "panel:launcher:open" }],
  declaration: () => "[agents-lanceur] Lanceur d'agents v1.0.0\n  Routes: agent/launch",
  component: ({ ctx }) => React.createElement(AgentLauncherPanel, { app: ctx.api }),
};
