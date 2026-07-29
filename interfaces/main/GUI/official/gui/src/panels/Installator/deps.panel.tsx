import React from 'react';
import { DependenciesPanel } from '../../panels/DependenciesPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "installator-deps", label: "Dépendances", icon: "checklist", version: "1.0.0",
  description: "Vérification et installation des dépendances système",
  daemonRoutes: [
    { route: "deps/check", methods: ["GET"], desc: "État des dépendances" },
    { route: "deps/install", methods: ["POST"], desc: "Installation" },
  ],
  menu: [{ menuPath: ["Fichier"], id: "deps:install", label: "Installer les dépendances", action: "panel:deps:install" }],
  declaration: () => "[installator-deps] Dépendances v1.0.0\n  Vérification et installation des dépendances système\n  Routes: deps/check, deps/install",
  component: ({ ctx }) => React.createElement(DependenciesPanel, { app: ctx.api }),
};
