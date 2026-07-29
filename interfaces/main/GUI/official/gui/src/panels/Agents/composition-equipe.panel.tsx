import React from 'react';
import { TeamCompositionPanel } from '../../panels/TeamCompositionPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "agents-composition-equipe", label: "Équipe", icon: "groups", version: "1.0.0",
  description: "Composition et gestion des équipes d'agents",
  daemonRoutes: [
    { route: "team/list", methods: ["GET"], desc: "Liste des équipes" },
    { route: "team/get", methods: ["GET"], desc: "Détail d'une équipe" },
    { route: "team/add-member", methods: ["POST"], desc: "Ajouter un membre" },
    { route: "team/init-workspace", methods: ["POST"], desc: "Initialiser un workspace" },
  ],
  menu: [
    { menuPath: ["Équipe"], id: "team:new", label: "Nouvelle équipe…", action: "panel:team:new" },
    { menuPath: ["Équipe"], id: "team:init-ws", label: "Initialiser le workspace", action: "panel:team:init-ws" },
  ],
  declaration: () => "[agents-composition-equipe] Équipe v1.0.0\n  Routes: team/list, team/get, team/add-member, team/init-workspace",
  component: ({ ctx }) => React.createElement(TeamCompositionPanel, { app: ctx.api }),
};
