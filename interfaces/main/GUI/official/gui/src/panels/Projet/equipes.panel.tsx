import React from 'react';
import { TeamCompositionPanel } from '../../panels/TeamCompositionPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "projet-equipes", label: "Équipes", icon: "groups", version: "1.0.0",
  description: "Gestion des équipes de projet",
  daemonRoutes: [
    { route: "team/list", methods: ["GET"], desc: "Liste équipes" },
    { route: "team/init-workspace", methods: ["POST"], desc: "Initialiser workspace" },
    { route: "team/create", methods: ["POST"], desc: "Créer équipe" },
  ],
  menu: [{ menuPath: ["Projet"], id: "team:create", label: "Créer une équipe…", action: "panel:team:create" }],
  declaration: () => "[projet-equipes] Équipes v1.0.0\n  Routes: team/list, team/init-workspace, team/create",
  component: ({ ctx }) => React.createElement(TeamCompositionPanel, { app: ctx.api }),
};
