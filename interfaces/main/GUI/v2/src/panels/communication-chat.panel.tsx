// communication/chat — session de chat LLM. Migré de V1 (découplé, sessions HTTP).
// Routes : chat/session/create|list|get|send|delete (POST JSON, sans SSE).

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  communication-chat:
    titre: "Chat"
    envoyer: "Envoyer"
    saisie: "Votre message…"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  communication-chat:
    titre: "Chat"
    envoyer: "Envoyer"
    saisie: "Votre message…"
    erreur: "Error"
`;


function ChatPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [session, setSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { data: sessions, reload } = usePoll<any>(
    ctx.api.post, 'chat/session/list', {}, 15000,
    (res) => res?.result?.sessions ?? [], true,
  );

  const send = async () => {
    if (!input.trim() || sending) return;
    setSending(true); setError(null);
    try {
      // 1. créer/récupérer une session
      let sid = session;
      if (!sid) {
        const created = await ctx.api.post('chat/session/create', { name: `chat-${Date.now()}` });
        sid = created?.result?.agent_id ?? created?.agent_id;
        setSession(sid);
      }
      // 2. envoyer le message
      const res = await ctx.api.post('chat/session/send', {
        agent_id: sid, content: input, role: 'user',
      });
      const reply = res?.result?.reply ?? res?.reply ?? '';
      setMessages((prev) => [...prev, { role: 'user', content: input }, { role: 'assistant', content: reply }]);
      setInput('');
      reload();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'hidden', padding: 8, boxSizing: 'border-box', fontSize: 12, display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0, marginBottom: 6 }}>
        {messages.length === 0 && <div style={{ color: '#64748b' }}>Nouvelle conversation…</div>}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 6 }}>
            <div style={{ fontWeight: 600, color: m.role === 'user' ? '#93c5fd' : '#4ade80' }}>{m.role === 'user' ? 'Vous' : 'Agent'}</div>
            <div style={{ color: '#e2e8f0', whiteSpace: 'pre-wrap' }}>{m.content}</div>
          </div>
        ))}
      </div>
      {error && <div style={{ color: '#f87171', marginBottom: 4 }}>{ctx.t?.('panels.communication-chat.erreur') ?? 'Erreur'} : {error}</div>}
      <div style={{ display: 'flex', gap: 6 }}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          rows={2} placeholder={ctx.t?.('panels.communication-chat.saisie') ?? 'Votre message…'}
          style={{ flex: 1, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: 6, color: 'var(--mw-fg, #e2e8f0)', fontSize: 12 }} />
        <button className="mw-btn" onClick={send} disabled={sending || !input.trim()}>{ctx.t?.('panels.communication-chat.envoyer') ?? 'Envoyer'}</button>
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'communication-chat',
  labelKey: 'panels.communication-chat.titre',
  iconKey: 'panels.communication-chat.titre',
  version: '1.0.0',
  essential: true,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[communication-chat] Chat v1.0.0\n  routes: chat/session/create, chat/session/send, chat/session/list',
  component: ChatPanel,
};

export const langFr = LANG_FR;