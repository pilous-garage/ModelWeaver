import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner, selectStyles } from '../components/ui.tsx';

export function ChatPanel({ app }: { app: AppApi }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem', overflow: 'hidden' }}>
      {/* Selector */}
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexShrink: 0 }}>
        <select value={app.chatProvider} onChange={e => { app.setChatProvider(e.target.value); app.setChatModel(''); }}
          style={{ ...selectStyles, padding: '0.3rem 0.5rem', width: '180px' }}>
          <option value="">-- Provider --</option>
          {app.providersList
            .filter((p: any) => p.available !== false)
            .map((p: any) => (
              <option key={p.ref} value={p.ref}>{p.name}</option>
            ))}
        </select>
        <select value={app.chatModel} onChange={e => app.setChatModel(e.target.value)}
          style={{ ...selectStyles, padding: '0.3rem 0.5rem', width: '200px' }}>
          <option value="">-- Modèle --</option>
          {app.modelsList
            .filter((m: any) => !app.chatProvider || m.provider_ref === app.chatProvider)
            .map((m: any) => (
              <option key={`${m.provider_ref}/${m.ref}`} value={m.ref}>{m.name || m.ref}</option>
            ))}
        </select>
        <button
          onClick={() => { app.setChatMessages([]); app.setChatInput(''); }}
          style={{ padding: '0.3rem 0.6rem', fontSize: '0.72rem', backgroundColor: '#475569', color: '#e2e8f0', border: '1px solid #64748b', borderRadius: '0.3rem', cursor: 'pointer' }}
        >Effacer</button>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.6rem', backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '0.8rem' }}>
        {app.chatMessages.length === 0 && (
          <div style={{ color: '#64748b', fontSize: '0.8rem', textAlign: 'center', marginTop: '2rem' }}>
            Sélectionnez un provider/modèle et envoyez un message.
          </div>
        )}
        {app.chatMessages.map((m, i) => (
          <div key={i} style={{
            maxWidth: '80%',
            alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
            backgroundColor: m.role === 'user' ? '#1d4ed8' : '#0f172a',
            border: `1px solid ${m.role === 'user' ? '#3b82f6' : '#334155'}`,
            borderRadius: '0.5rem',
            padding: '0.5rem 0.7rem',
            fontSize: '0.78rem',
            lineHeight: '1.4',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            <div style={{ fontWeight: '600', fontSize: '0.65rem', color: m.role === 'user' ? '#93c5fd' : '#64748b', marginBottom: '0.2rem' }}>
              {m.role === 'user' ? 'Vous' : 'Assistant'}
            </div>
            <div>{m.content}</div>
          </div>
        ))}
        {app.chatSending && (
          <div style={{ maxWidth: '80%', alignSelf: 'flex-start', backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.5rem', padding: '0.5rem 0.7rem', fontSize: '0.78rem' }}>
            <Spinner size={14} color="#94a3b8" /> Réflexion…
          </div>
        )}
      </div>

      {/* Input */}
      <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
        <input
          value={app.chatInput}
          onChange={e => app.setChatInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); app.handleChatSend(); } }}
          placeholder="Votre message…"
          disabled={app.chatSending}
          style={{
            flex: 1, padding: '0.5rem 0.7rem', backgroundColor: '#1e293b', color: '#e2e8f0',
            border: '1px solid #475569', borderRadius: '0.375rem', fontSize: '0.8rem', outline: 'none',
          }}
        />
        <button
          onClick={app.handleChatSend}
          disabled={!app.chatProvider || !app.chatModel || !app.chatInput.trim() || app.chatSending}
          style={{
            padding: '0.5rem 1rem', backgroundColor: app.chatSending ? '#1e293b' : '#2563eb',
            color: app.chatSending ? '#64748b' : 'white', border: 'none', borderRadius: '0.375rem',
            fontSize: '0.78rem', fontWeight: '600', cursor: app.chatSending ? 'default' : 'pointer',
            display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
          }}
        >
          {app.chatSending ? <Spinner size={14} color="#64748b" /> : '→'} Envoyer
        </button>
      </div>
    </div>
  );
}
