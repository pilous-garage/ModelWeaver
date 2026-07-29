import React from 'react';
import { InstalledToolsPanel } from '../../panels/InstalledToolsPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "installator-outils-installes", label: "Outils installés", icon: "build", version: "1.0.0",
  description: "Outils système installés (opencode, litellm…)",
  daemonRoutes: [
    { route: "tools/installed/list", methods: ["GET"], desc: "Liste des outils" },
    { route: "tools/install", methods: ["POST"], desc: "Installer un outil" },
  ],
  menu: [{ menuPath: ["Affichage"], id: "tools:refresh", label: "Rafraîchir la liste", action: "panel:tools:refresh" }],
  declaration: () => "[installator-outils-installes] Outils installés v1.0.0\n  Routes: tools/installed/list, tools/install",
  component: ({ ctx }) => React.createElement(InstalledToolsPanel, { app: ctx.api }),
};
