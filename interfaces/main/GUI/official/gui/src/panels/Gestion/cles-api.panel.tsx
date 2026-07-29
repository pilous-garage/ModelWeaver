import React from 'react';
import { KeysPanel } from '../../panels/KeysPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "gestion-cles-api", label: "Clés API", icon: "key", version: "1.0.0",
  description: "Gestion des clés API des providers",
  daemonRoutes: [
    { route: "keys/list", methods: ["GET"], desc: "Liste des clés" },
    { route: "keys/set", methods: ["POST"], desc: "Ajouter/modifier" },
    { route: "keys/delete", methods: ["POST"], desc: "Supprimer" },
    { route: "keys/set_lock", methods: ["POST"], desc: "Verrouiller/déverrouiller" },
  ],
  menu: [{ menuPath: ["Fichier"], id: "keys:add", label: "Ajouter une clé…", action: "panel:keys:add" }],
  declaration: () => "[gestion-cles-api] Clés API v1.0.0\n  Routes: keys/list, keys/set, keys/delete, keys/set_lock",
  component: ({ ctx }) => React.createElement(KeysPanel, { app: ctx.api }),
};
