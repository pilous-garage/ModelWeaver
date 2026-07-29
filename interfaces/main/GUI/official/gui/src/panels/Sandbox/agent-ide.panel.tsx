import React from 'react';
import { AgentSandboxIDE } from '../../components/AgentSandboxIDE.tsx';
import type { PanelDef } from '../../types.ts';

export const Panel: PanelDef = {
  id: "sandbox-agent-ide",
  label: "Agent IDE",
  icon: "terminal",
  version: "1.0.0",
  description: "IDE de création et modification d'agents, skills, comportements",
  daemonRoutes: [
    { route: "catalogue/agents/list", methods: ["GET"], desc: "Liste des agents" },
    { route: "catalogue/agents/get", methods: ["GET"], desc: "Détail agent" },
    { route: "catalogue/agents/save", methods: ["POST"], desc: "Sauvegarder agent" },
    { route: "catalogue/skills/list", methods: ["GET"], desc: "Liste des skills" },
    { route: "catalogue/skills/get", methods: ["GET"], desc: "Détail skill" },
    { route: "catalogue/roles/list", methods: ["GET"], desc: "Liste des rôles" },
  ],
  menu: [
    { menuPath: ["Fichier"], id: "ide:new-agent", label: "Nouvel agent", action: "panel:ide:new-agent" },
    { menuPath: ["Fichier"], id: "ide:new-skill", label: "Nouveau skill", action: "panel:ide:new-skill" },
    { menuPath: ["Fichier"], id: "ide:save", label: "Enregistrer", shortcut: "Ctrl+S", action: "panel:ide:save" },
  ],
  declaration: () => [
    "[sandbox-agent-ide] Agent IDE v1.0.0",
    "  IDE de création et modification d'agents, skills, comportements, rôles",
    "  Routes: catalogue/agents/*, catalogue/skills/*, catalogue/roles/*",
    "  Menu: Fichier > Nouvel agent, Nouveau skill, Enregistrer",
  ].join("\n"),
  component: () => React.createElement(AgentSandboxIDE),
};
