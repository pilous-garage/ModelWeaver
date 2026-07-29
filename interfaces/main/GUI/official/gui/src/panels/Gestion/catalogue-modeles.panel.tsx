import React from 'react';
import { CataloguePanel } from '../../panels/CataloguePanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "gestion-catalogue-modeles", label: "Catalogue", icon: "database", version: "1.0.0",
  description: "Catalogue des modèles LLM disponibles",
  daemonRoutes: [
    { route: "llm/models/list", methods: ["GET"], desc: "Modèles par provider" },
    { route: "providers/list", methods: ["GET"], desc: "Liste des providers" },
    { route: "llm/recommend", methods: ["GET"], desc: "Recommandation modèle" },
  ],
  menu: [],
  declaration: () => "[gestion-catalogue-modeles] Catalogue modèles v1.0.0\n  Routes: llm/models/list, providers/list",
  component: ({ ctx }) => React.createElement(CataloguePanel, { app: ctx.api }),
};
