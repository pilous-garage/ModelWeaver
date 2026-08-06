// communication/dev-chat — chat de développement piloté par l'agent maître.
// Modes plan (READ-ONLY + plan.md + tâches) / build (exécution + complétion).
// Sélecteur provider/modèle (llm/models/list) + « qui répond » + horodatage
// (heure d'envoi, heure de fin, durée) — style opencode (▣ agent · model · durée).
// Routes daemon : dev-chat/send, llm/models/list.

import React, { useEffect, useRef, useState } from 'react';
import type { PanelDef } from './contract.ts';
import { ModelPicker } from '../components/ModelPicker.tsx';

const LANG_FR = `
panels:
  communication-dev-chat:
    titre: "Chat de dev"
    placeholder: "Décrivez une tâche de développement…"
    envoyer: "Envoyer"
    mode_plan: "Plan"
    mode_build: "Build"
    workspace: "Workspace"
    provider: "Provider"
    modele: "Modèle"
    auto: "Auto"
    erreur: "Erreur"
    en_cours: "En cours…"
    vous: "Vous"
    assistant: "Assistant"
    envoi: "Envoyé"
    termine: "Terminé"
`;

const LANG_EN = `
panels:
  communication-dev-chat:
    titre: "Dev chat"
    placeholder: "Describe a dev task…"
    envoyer: "Send"
    mode_plan: "Plan"
    mode_build: "Build"
    workspace: "Workspace"
    provider: "Provider"
    modele: "Model"
    auto: "Auto"
    erreur: "Error"
    en_cours: "Working…"
    vous: "You"
    assistant: "Assistant"
    envoi: "Sent"
    termine: "Done"
`;

interface Msg {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;  // bloc de raisonnement du modèle (modèles raisonneurs)
  mode?: string;
  ts?: number;        // heure d'envoi (user) / heure de fin (assistant)
  provider?: string;  // qui répond
  model?: string;
  durationMs?: number;
  streamed?: boolean; // la réponse a été affichée en temps réel
  finished?: boolean; // la réponse est terminée (plus d'échange en cours)
  error?: boolean;    // la réponse s'est terminée sur une erreur
}

function fmtTime(ts?: number): string {
  if (!ts) return '';
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// « Qui répond » : le model_ref peut déjà être préfixé du provider
// (ex. opencode-zen/deepseek-v4-flash-free) → on évite d'afficher le doublon
// `opencode-zen/opencode-zen/...` : si le modèle commence par `provider/`, on
// n'affiche que la partie modèle.
function fmtWho(provider?: string, model?: string): string {
  const p = provider || '—';
  if (!model) return p;
  return model.startsWith(`${p}/`) ? model : `${p}/${model}`;
}

function fmtDuration(ms?: number): string {
  if (ms == null) return '';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function DevChatPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const workspaceId = params.workspace_id ?? 'mw-dev-chat';
  const [session] = useState(() => params.session ?? `devchat_${Date.now().toString(36)}`);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [mode, setMode] = useState<'plan' | 'build'>('plan');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showThinking, setShowThinking] = useState<Record<number, boolean>>({});
  const [provider, setProvider] = useState('');   // '' = auto
  const [model, setModel] = useState('');
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [messages, busy]);

  // Animation du curseur streaming (injectée une fois, partagée par tous les messages).
  useEffect(() => {
    if (document.getElementById('mw-blink-style')) return;
    const st = document.createElement('style');
    st.id = 'mw-blink-style';
    st.textContent = '@keyframes mw-blink{0%,49%{opacity:1}50%,100%{opacity:0}}';
    document.head.appendChild(st);
  }, []);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput('');
    setBusy(true);
    setError(null);
    const sentTs = Date.now();
    // Index stable de la réponse assistant : messages.length = index du message
    // user qu'on ajoute maintenant, +1 = l'assistant (append en un seul set).
    const userIdx = messages.length;
    const liveIdx = userIdx + 1;
    const liveMsg: Msg = { role: 'assistant', content: '', mode, provider, model };
    setMessages((prev) => [...prev, { role: 'user', content: text, mode, ts: sentTs }, liveMsg]);

    try {
      if (ctx.api?.stream) {
        // Streaming réel : event delta (thinking/content) puis event result.
        await new Promise<void>((resolve) => {
          ctx.api.stream('dev-chat/stream', {
            message: text, mode, session, workspace_id: workspaceId,
            provider_ref: provider, model_ref: model,
          }, (ev: any) => {
            const { event, data } = ev ?? {};
            if (event === 'delta' && data) {
              const kind = data.kind ?? 'content';
              const chunk = data.chunk ?? '';
              if (kind === 'thinking') {
                setMessages((prev) => prev.map((m, i) => i === liveIdx ? { ...m, thinking: (m.thinking ?? '') + chunk } : m));
              } else {
                setMessages((prev) => prev.map((m, i) => i === liveIdx ? { ...m, content: (m.content ?? '') + chunk, streamed: true } : m));
              }
            } else if (event === 'result' && data) {
              const doneTs = Date.now();
              setMessages((prev) => prev.map((m, i) => i === liveIdx ? {
                ...m,
                provider: m.provider || data.provider_ref,
                model: m.model || data.model_ref,
                durationMs: data.duration_ms ?? doneTs - sentTs,
                ts: doneTs,
                finished: true,
                content: (m.content?.trim() || undefined) ? m.content : (data.reply ?? data.content ?? (data.error ?? '')),
              } : m));
              if (data.error) setError(String(data.error));
            } else if (event === 'done') {
              resolve();
            } else if (event === 'error') {
              const msg = data?.error ?? 'erreur de flux';
              setMessages((prev) => prev.map((m, i) => i === liveIdx ? { ...m, finished: true, error: true, ts: Date.now() } : m));
              if (!data?.done) setError(String(msg));
              resolve();
            }
          });
        });
      } else {
        // Repli : dev-chat/send synchrone (pas de streaming).
        const res = await ctx.api.post('dev-chat/send', {
          message: text, mode, session, workspace_id: workspaceId,
          provider_ref: provider, model_ref: model,
        });
        const r = res?.result ?? res ?? {};
        const reply = r.reply ?? r.content ?? '';
        const doneTs = Date.now();
        setMessages((prev) => prev.map((m, i) => i === liveIdx ? {
          ...m, content: reply || (r.error || '(pas de réponse)'),
          provider: r.provider_ref, model: r.model_ref, ts: doneTs,
          durationMs: r.duration_ms ?? doneTs - sentTs,
          finished: true, error: !!r.error,
        } : m));
        if (r.error) setError(String(r.error));
      }
    } catch (e: any) {
      setMessages((prev) => prev.map((m, i) => i === liveIdx ? {
        ...m, content: `Erreur : ${String(e?.message ?? e)}`, ts: Date.now(),
        durationMs: Date.now() - sentTs, finished: true, error: true,
      } : m));
    } finally {
      setBusy(false);
    }
  };

  const toggleThinking = (idx: number) => {
    setShowThinking((s) => ({ ...s, [idx]: !s[idx] }));
  };

  const modeStyle = (m: string) => ({
    padding: '3px 10px', fontSize: 11, cursor: 'pointer', borderRadius: 4, border: '1px solid var(--mw-border, #334155)',
    background: mode === m ? 'var(--mw-accent, #3b82f6)' : 'transparent',
    color: mode === m ? '#fff' : '#94a3b8',
  });

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {/* Barre de contrôle : mode + provider/modèle + workspace */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
        <button data-testid="dev-chat-mode-plan" onClick={() => setMode('plan')} style={modeStyle('plan')}>
          {ctx.t?.('panels.communication-dev-chat.mode_plan') ?? 'Plan'}
        </button>
        <button data-testid="dev-chat-mode-build" onClick={() => setMode('build')} style={modeStyle('build')}>
          {ctx.t?.('panels.communication-dev-chat.mode_build') ?? 'Build'}
        </button>
        <ModelPicker provider={provider} model={model} onProvider={setProvider} onModel={setModel} api={ctx.api} t={ctx.t} />
        <span style={{ color: '#64748b', fontSize: 11 }}>{workspaceId}</span>
      </div>

      {/* Historique */}
      <div ref={bodyRef} style={{ flex: 1, minHeight: 0, overflow: 'auto', marginBottom: 6 }}>
        {messages.length === 0 && !busy && (
          <div style={{ color: '#64748b', textAlign: 'center', padding: 20 }}>
            {ctx.t?.('panels.communication-dev-chat.placeholder') ?? 'Décrivez une tâche de développement…'}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 8 }}>
            {/* En-tête : qui + heure (style opencode ▣) */}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11 }}>
              <span style={{ fontWeight: 700, color: m.role === 'user' ? '#3b82f6' : '#4ade80' }}>
                {m.role === 'user' ? (ctx.t('panels.communication-dev-chat.vous') ?? 'Vous') : (ctx.t('panels.communication-dev-chat.assistant') ?? 'Assistant')}
              </span>
              {m.role === 'user' && <span style={{ color: '#64748b' }}>{fmtTime(m.ts)}</span>}
              {m.role === 'assistant' && (
                <>
                  <span style={{ color: '#64748b' }}>▣ {m.mode} · {fmtWho(m.provider, m.model)}</span>
                  <span style={{ color: '#64748b' }}>· {fmtTime(m.ts)} · {fmtDuration(m.durationMs)}</span>
                  {m.streamed && <span style={{ color: '#38bdf8', fontSize: 10 }}>⚡</span>}
                </>
              )}
            </div>
            {/* Bloc thinking (raisonnement cliquable — style opencode) */}
            {m.role === 'assistant' && !!m.thinking && (
              <div style={{ marginTop: 4 }}>
                <div
                  onClick={() => toggleThinking(i)}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, color: '#818cf8', userSelect: 'none' }}>
                  <span style={{ display: 'inline-block', transition: 'transform .15s', transform: showThinking[i] ? 'rotate(90deg)' : 'none' }}>▸</span>
                  <span>{showThinking[i] ? 'Pensé' : 'Penser…'}</span>
                </div>
                {showThinking[i] && (
                  <div style={{ marginTop: 2, padding: '6px 8px', background: 'var(--mw-bg-dim, rgba(129,140,248,.06))', borderLeft: '2px solid #818cf8', color: '#a5b4fc', whiteSpace: 'pre-wrap', fontSize: 11, borderRadius: 4 }}>
                    {m.thinking}
                  </div>
                )}
              </div>
            )}
            {/* Contenu */}
            <div style={{ color: 'var(--mw-fg-dim, #cbd5e1)', whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 2 }}>
              {m.content || (busy && i === messages.length - 1 ? (ctx.t('panels.communication-dev-chat.en_cours') ?? 'En cours…') : '')}
              {busy && i === messages.length - 1 && (
                <span style={{ display: 'inline-block', width: 7, height: 12, marginLeft: 2, background: '#38bdf8', verticalAlign: 'text-bottom', animation: 'mw-blink 1s steps(2,start) infinite' }}>
                </span>
              )}
            </div>
            {/* Fin de réponse : marqueur explicite « réponse terminée » */}
            {m.role === 'assistant' && m.finished && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4, fontSize: 10 }}>
                <span style={{ color: m.error ? '#f87171' : '#34d399', fontWeight: 600 }}>
                  {m.error ? '✕' : '✓'}
                </span>
                <span style={{ color: m.error ? '#f87171' : '#34d399' }}>
                  {m.error
                    ? (ctx.t('panels.communication-dev-chat.erreur') ?? 'Erreur')
                    : (ctx.t('panels.communication-dev-chat.termine') ?? 'Terminé')}
                </span>
              </div>
            )}
          </div>
        ))}
      </div>

      {error && <div style={{ color: '#f87171', fontSize: 11, marginBottom: 4 }}>{ctx.t('panels.communication-dev-chat.erreur') ?? 'Erreur'} : {error}</div>}

      {/* Saisie */}
      <div style={{ display: 'flex', gap: 4 }}>
        <input
          data-testid="dev-chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(); }}
          placeholder={ctx.t('panels.communication-dev.chat.placeholder') ?? 'Décrivez une tâche…'}
          style={{ flex: 1, fontSize: 12, padding: '5px 8px', background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6 }}
        />
        <button data-testid="dev-chat-send" onClick={send} disabled={busy || !input.trim()}
          style={{ padding: '5px 14px', fontSize: 12, cursor: 'pointer', background: 'var(--mw-accent, #3b82f6)', color: '#fff', border: 'none', borderRadius: 6 }}>
          {ctx.t('panels.communication-dev-chat.envoyer') ?? 'Envoyer'}
        </button>
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'communication-dev-chat',
  labelKey: 'panels.communication-dev-chat.titre',
  iconKey: 'panels.communication-dev-chat.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['communication', 'projet'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[communication-dev-chat] Chat de dev v1.0.0\n  routes: dev-chat/send, llm/models/list',
  component: DevChatPanel,
};

export const langFr = LANG_FR;
