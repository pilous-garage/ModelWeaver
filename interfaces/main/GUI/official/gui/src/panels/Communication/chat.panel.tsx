import React from 'react';
import { ChatPanel } from '../../panels/ChatPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "communication-chat", label: "Chat", icon: "chat", version: "1.0.0",
  description: "Session de chat avec un LLM",
  daemonRoutes: [
    { route: "chat/session/list", methods: ["GET"], desc: "Liste des sessions" },
    { route: "chat/session/get", methods: ["GET"], desc: "Historique" },
    { route: "chat/session/send", methods: ["POST"], desc: "Envoyer un message" },
    { route: "chat/session/delete", methods: ["POST"], desc: "Supprimer une session" },
  ],
  menu: [
    { menuPath: ["Chat"], id: "chat:new", label: "Nouvelle conversation", action: "panel:chat:new" },
    { menuPath: ["Chat"], id: "chat:clear", label: "Effacer", action: "panel:chat:clear" },
  ],
  declaration: () => "[communication-chat] Chat v1.0.0\n  Routes: chat/session/*",
  component: ({ ctx }) => React.createElement(ChatPanel, { app: ctx.api }),
};
