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

interface Seg {
  kind: 'thinking' | 'content' | 'tool';
  text: string;
  // Timer de la section : début (ms epoch) + durée mesurée en live.
  startTs?: number;
  endTs?: number;   // figé à la fin de la section (corrige le timer)
  live?: boolean;   // la section est en cours (timer qui tourne)
  toolOk?: boolean; // pour kind 'tool' : succès ou échec
}

interface LlmLine {
  tag: string;    // 'branché' | 'fall-back' | 'retour'
  model: string;  // provider/modèle
  ts: number;
}

interface Msg {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;  // bloc de raisonnement du modèle (modèles raisonneurs)
  segments?: Seg[];   // ordre d'arrivée réel du flux (thinking/content/tool entremêlés)
  llmLines?: LlmLine[]; // journal des changements de modèle (fall-back / retour)
  status?: string;    // état en cours : 'contact' | 'pense' | 'outil' | 'erreur'
  mode?: string;
  ts?: number;        // heure d'envoi (user) / heure de fin (assistant)
  sentTs?: number;    // heure d'envoi de la requête (début du time_elapsed)
  provider?: string;  // qui répond
  model?: string;
  durationMs?: number;
  streamed?: boolean; // la réponse a été affichée en temps réel
  finished?: boolean; // la réponse est terminée (plus d'échange en cours)
  error?: boolean;    // la réponse s'est terminée sur une erreur
  queued?: boolean;   // message en file d'attente (pas encore envoyé)
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

// Durée d'une section : live (timer) ou figée (endTs-startTs).
function fmtSection(seg: Seg, now: number): string {
  const start = seg.startTs ?? seg.endTs;
  if (!start) return '';
  const end = seg.endTs ?? (seg.live ? now : start);
  return fmtDuration(Math.max(0, end - start));
}

// Hooks timer : un tick toutes les 250ms tant qu'une réponse est en cours
// (segments live ou message non finished) → les durées se mettent à jour.
function useNow(live: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [live]);
  return now;
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
  const [activeModel, setActiveModel] = useState(() => {
    // Initialisé au modèle choisi dans les menus (au premier rendu), mis à
    // jour ensuite par les événements 'llm' du flux (fallback / retour).
    return (provider && model) ? fmtWho(provider, model) : '';
  });
  const [queue, setQueue] = useState<string[]>([]);   // messages en attente (busy)
  const abortRef = useRef<{ abort: () => void } | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Timer global : tick UNIQUEMENT tant qu'un message assistant est EN COURS
  // (pas fini) — quand la réponse se termine, le ⏱ est figé sur durationMs.
  const anyLive = messages.some((m) => !m.finished && m.role === 'assistant');
  const now = useNow(anyLive);

  // Badge du modèle branché : par défaut = modèle choisi dans les menus,
  // mis à jour par les événements 'llm' (fallback / retour) du flux.
  useEffect(() => {
    if (provider && model) setActiveModel(fmtWho(provider, model));
  }, [provider, model]);

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

  const send = async (textOverride?: string) => {
    const text = (textOverride ?? input).trim();
    if (!text || busy) return;
    if (textOverride) {
      // Départ depuis la file d'attente : on retire le message traité.
      setQueue((q) => q.filter((m) => m !== text));
    }
    setInput('');
    setBusy(true);
    setError(null);
    const sentTs = Date.now();
    // Si le message était en file d'attente (queued), on remplace ce cadre par
    // le vrai message user (même contenu) — sinon on l'ajoute.
    const wasQueued = messages.some((x) => x.queued && x.content === text);
    setMessages((prev) => {
      if (wasQueued) {
        return prev.map((x) => x.queued && x.content === text
          ? { role: 'user', content: text, mode, ts: sentTs, sentTs }
          : x);
      }
      return [...prev, { role: 'user', content: text, mode, ts: sentTs, sentTs }];
    });
    // L'assistant est TOUJOURS le dernier message après l'ajout.
    const userIdx = wasQueued ? messages.length - 1 : messages.length;
    const liveIdx = userIdx + 1;
    setMessages((prev) => [...prev, { role: 'assistant', content: '', mode, provider, model, sentTs, status: 'contact' }]);

    try {
      if (ctx.api?.stream) {
        // Streaming réel : event delta (thinking/content) puis event result.
        await new Promise<void>((resolve) => {
          let doAbort: (() => void) | null = null;
          const abort = () => { if (doAbort) doAbort(); };
          abortRef.current = { abort };
          const ret = ctx.api.stream('dev-chat/stream', {
            message: text, mode, session, workspace_id: workspaceId,
            provider_ref: provider, model_ref: model,
          }, (ev: any) => {
            const { event, data } = ev ?? {};
            if (event === 'delta' && data) {
              const kind = data.kind ?? 'content';
              const chunk = data.chunk ?? '';
              if (kind === 'llm' && chunk) {
                // Ligne console : branché / fall-back / retour / err (erreur).
                // Format : "<tag> <provider>/<model> [catégorie] [message]".
                const sp = chunk.indexOf(' ');
                const tag = sp > 0 ? chunk.slice(0, sp) : 'branché';
                const rest = sp > 0 ? chunk.slice(sp + 1) : chunk;
                if (tag === 'err') {
                  // Erreur LLM avant fall-back : on l'affiche en rouge.
                  setMessages((prev) => prev.map((m, i) => {
                    if (i !== liveIdx) return m;
                    const lines = m.llmLines ? [...m.llmLines] : [];
                    lines.push({ tag: 'err', model: rest, ts: Date.now() });
                    return { ...m, llmLines: lines, status: 'erreur' };
                  }));
                  return;
                }
                setActiveModel(rest);
                setMessages((prev) => prev.map((m, i) => {
                  if (i !== liveIdx) return m;
                  const lines = m.llmLines ? [...m.llmLines] : [];
                  lines.push({ tag, model: rest, ts: Date.now() });
                  return { ...m, llmLines: lines, status: tag === 'fall-back' ? 'erreur' : (m.status || 'contact') };
                }));
                return;
              }
              // Tool call / résultat : section 'tool'.
              if (kind === 'tool' && chunk) {
                const sp = chunk.indexOf(' ');
                const tkind = sp > 0 ? chunk.slice(0, sp) : 'call'; // call|ok|err
                const rest = sp > 0 ? chunk.slice(sp + 1) : chunk;
                setMessages((prev) => prev.map((m, i) => {
                  if (i !== liveIdx) return m;
                  const segs = m.segments ? [...m.segments] : [];
                  const isResult = tkind === 'ok' || tkind === 'err';
                  // Une ligne de résultat 'ok/err' termine la section 'call'.
                  if (isResult && segs.length > 0 && segs[segs.length - 1].kind === 'tool' && segs[segs.length - 1].live) {
                    segs[segs.length - 1] = {
                      ...segs[segs.length - 1],
                      text: segs[segs.length - 1].text + '\n' + (tkind === 'ok' ? '✓ ' : '✕ ') + rest,
                      endTs: Date.now(), live: false, toolOk: tkind === 'ok',
                    };
                  } else {
                    segs.push({ kind: 'tool', text: (tkind === 'call' ? '⚙ ' : '') + rest, startTs: Date.now(), live: true, toolOk: tkind === 'ok' });
                  }
                  return { ...m, segments: segs, streamed: true, status: 'outil' };
                }));
                return;
              }
              setMessages((prev) => prev.map((m, i) => {
                if (i !== liveIdx) return m;
                const segs = m.segments ? [...m.segments] : [];
                // Fusionne les morceaux consécutifs de même nature (thinking→
                // thinking, content→content) pour éviter des blocs clignotants,
                // MAIS conserve l'ordre d'arrivée réel entre thinking et content.
                const lastIdx = segs.length - 1;
                if (chunk && lastIdx >= 0 && segs[lastIdx].kind === kind) {
                  segs[lastIdx] = { ...segs[lastIdx], text: segs[lastIdx].text + chunk };
                } else if (chunk) {
                  segs.push({ kind, text: chunk, startTs: Date.now(), live: true });
                }
                if (kind === 'thinking') {
                  return { ...m, thinking: (m.thinking ?? '') + chunk, segments: segs, status: 'pense' };
                }
                return { ...m, content: (m.content ?? '') + chunk, streamed: true, segments: segs, status: 'génère' };
              }));
            } else if (event === 'result' && data) {
              const doneTs = Date.now();
              if (data.provider_ref && data.model_ref) {
                setActiveModel(fmtWho(data.provider_ref, data.model_ref));
              }
              setMessages((prev) => prev.map((m, i) => i === liveIdx ? {
                ...m,
                provider: m.provider || data.provider_ref,
                model: m.model || data.model_ref,
                durationMs: data.duration_ms ?? doneTs - sentTs,
                ts: doneTs,
                finished: true,
                content: (m.content?.trim() || undefined) ? m.content : (data.reply ?? data.content ?? (data.error ?? '')),
                // Figer les sections encore live (correction du timer).
                segments: (m.segments || []).map((s) => s.live ? { ...s, endTs: doneTs, live: false } : s),
              } : m));
              if (data.error) setError(String(data.error));
              abortRef.current = null;
              resolve();
            } else if (event === 'done') {
              abortRef.current = null;
              resolve();
            } else if (event === 'error') {
              const msg = data?.error ?? 'erreur de flux';
              const doneTs = Date.now();
              setMessages((prev) => prev.map((m, i) => i === liveIdx ? {
                ...m, finished: true, error: true, ts: doneTs,
                segments: (m.segments || []).map((s) => s.live ? { ...s, endTs: doneTs, live: false } : s),
              } : m));
              if (!data?.done) setError(String(msg));
              abortRef.current = null;
              resolve();
            }
          });
          // Capture le handle d'abort retourné par daemonPostStream.
          if (ret && typeof ret.then === 'function') {
            ret.then((abortFn: any) => { if (abortFn) doAbort = abortFn; });
          } else if (typeof ret === 'function') {
            doAbort = ret;
          }
          // Interruption par l'utilisateur (bouton / poubelle).
          abortRef.current = {
            abort: () => {
              if (doAbort) doAbort();
              const endTs = Date.now();
              setMessages((prev) => prev.map((m, i) => i === liveIdx ? {
                ...m, finished: true, ts: endTs, durationMs: endTs - sentTs,
                content: (m.content?.trim() || undefined) ? m.content : (m.content || '⏹ Interrompu'),
                segments: (m.segments || []).map((s) => s.live ? { ...s, endTs, live: false } : s),
              } : m));
              abortRef.current = null;
              resolve();
            },
          };
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
      // Lancer le message suivant de la file d'attente s'il y en a un.
      if (queue.length > 0) {
        const next = queue[0];
        setQueue((q) => q.slice(1));
        setTimeout(() => send(next), 50);
      }
    }
  };

  // Bouton d'envoi : si busy → mettre en file d'attente (message queued).
  const submit = () => {
    const text = input.trim();
    if (!text) return;
    if (busy) {
      setQueue((q) => [...q, text]);
      // Affiche un message « queued » sous la réponse en cours.
      setMessages((prev) => [...prev, {
        role: 'assistant', content: text, mode, provider, model,
        queued: true, ts: Date.now(), sentTs: Date.now(),
      }]);
      setInput('');
    } else {
      send();
    }
  };

  const interrupt = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
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
        {/* Modèle actuellement branché (peut différer des menus après fallback) */}
        {activeModel && (
          <span title="Modèle actuellement branché"
            style={{ fontSize: 10, fontFamily: 'monospace', color: '#38bdf8', background: 'rgba(56,189,248,.12)', padding: '2px 6px', borderRadius: 4, border: '1px solid rgba(56,189,248,.3)' }}>
            ● {activeModel}
          </span>
        )}
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
                  {/* Status en cours : contact / pense / outil / génère / erreur */}
                  {m.status && !m.finished && (
                    <span style={{
                      fontSize: 10, padding: '1px 6px', borderRadius: 3,
                      background: m.status === 'erreur' ? 'rgba(248,113,113,.15)' : 'rgba(56,189,248,.12)',
                      color: m.status === 'erreur' ? '#f87171' : '#38bdf8',
                      border: `1px solid ${m.status === 'erreur' ? 'rgba(248,113,113,.3)' : 'rgba(56,189,248,.3)'}`,
                    }}>
                      {m.status === 'contact' ? '📞 contact…' : m.status === 'pense' ? '🧠 pense…' : m.status === 'outil' ? '⚙ outil…' : m.status === 'génère' ? '✍ génère…' : m.status}
                    </span>
                  )}
                </>
              )}
            </div>
            {/* Contenu + thinking + tool dans l'ORDRE d'arrivée du flux (entremêlés) */}
            {m.role === 'assistant' && m.segments && m.segments.length > 0 && (
              <div style={{ marginTop: 2 }}>
                {m.segments.map((seg, si) => {
                  const segDur = fmtSection(seg, now);
                  const durLabel = segDur ? <span style={{ fontSize: 9, color: '#475569', marginLeft: 6 }}>{segDur}</span> : null;
                  if (seg.kind === 'thinking') {
                    return (
                      <div key={si} style={{ marginBottom: 2 }}>
                        <div
                          onClick={() => toggleThinking(i)}
                          style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, color: '#818cf8', userSelect: 'none' }}>
                          <span style={{ display: 'inline-block', transition: 'transform .15s', transform: showThinking[i] ? 'rotate(90deg)' : 'none' }}>▸</span>
                          <span>{showThinking[i] ? 'Pensé' : 'Penser…'}</span>
                          {durLabel}
                        </div>
                        {showThinking[i] && (
                          <div style={{ marginTop: 2, padding: '6px 8px', background: 'var(--mw-bg-dim, rgba(129,140,248,.06))', borderLeft: '2px solid #818cf8', color: '#a5b4fc', whiteSpace: 'pre-wrap', fontSize: 11, borderRadius: 4 }}>
                            {seg.text}
                          </div>
                        )}
                      </div>
                    );
                  }
                  if (seg.kind === 'tool') {
                    const ok = seg.toolOk !== false;
                    return (
                      <div key={si} style={{ marginBottom: 2, fontSize: 11, fontFamily: 'monospace', color: ok ? '#7dd3fc' : '#f87171', background: 'rgba(125,211,252,.05)', border: '1px solid rgba(125,211,252,.15)', borderRadius: 4, padding: '2px 6px', whiteSpace: 'pre-wrap' }}>
                        {seg.text}{durLabel}
                      </div>
                    );
                  }
                  return (
                    <div key={si} style={{ color: 'var(--mw-fg-dim, #cbd5e1)', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                      {seg.text}
                    </div>
                  );
                })}
                {busy && i === messages.length - 1 && (
                  <span style={{ display: 'inline-block', width: 7, height: 12, marginLeft: 2, background: '#38bdf8', verticalAlign: 'text-bottom', animation: 'mw-blink 1s steps(2,start) infinite' }}></span>
                )}
              </div>
            )}
            {/* Journal des changements de modèle (fall-back / retour / erreur) */}
            {m.role === 'assistant' && m.llmLines && m.llmLines.length > 0 && (
              <div style={{ marginTop: 3, display: 'flex', flexDirection: 'column', gap: 2 }}>
                {m.llmLines.map((ll, li) => (
                  <div key={li} style={{
                    fontSize: 10, fontFamily: 'monospace',
                    color: ll.tag === 'fall-back' ? '#fbbf24' : ll.tag === 'retour' ? '#34d399' : ll.tag === 'err' ? '#f87171' : '#38bdf8',
                  }}>
                    {ll.tag === 'fall-back' ? '↷' : ll.tag === 'retour' ? '↺' : ll.tag === 'err' ? '✕' : '●'} {ll.tag} <b>{ll.model}</b> {fmtTime(ll.ts)}
                  </div>
                ))}
              </div>
            )}
            {/* Repli : pas de segments (réponse sync/init) → thinking puis contenu */}
            {!(m.segments && m.segments.length > 0) && (
              <>
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
              </>
            )}
            <div style={{ clear: 'both' }} />
            {/* Fin de réponse : marqueur explicite « réponse terminée » + time_elapsed */}
            {m.role === 'assistant' && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4, fontSize: 10 }}>
                {m.finished ? (
                  <>
                    <span style={{ color: m.error ? '#f87171' : '#34d399', fontWeight: 600 }}>
                      {m.error ? '✕' : '✓'}
                    </span>
                    <span style={{ color: m.error ? '#f87171' : '#34d399' }}>
                      {m.error
                        ? (ctx.t('panels.communication-dev-chat.erreur') ?? 'Erreur')
                        : (ctx.t('panels.communication-dev-chat.termine') ?? 'Terminé')}
                      {m.ts ? ` · ${fmtTime(m.ts)}` : ''}
                    </span>
                  </>
                ) : busy && i === messages.length - 1 ? (
                  <button onClick={interrupt}
                    style={{ fontSize: 10, cursor: 'pointer', padding: '1px 8px', borderRadius: 4, border: '1px solid #f87171', color: '#f87171', background: 'transparent' }}>
                    ⏹ Interrompre
                  </button>
                ) : null}
                {/* time_elapsed : depuis l'envoi de la requête */}
                <span style={{ color: '#64748b', fontFamily: 'monospace' }}>
                  ⏱ {m.finished
                    ? fmtDuration(m.durationMs ?? (m.sentTs ? Date.now() - m.sentTs : 0))
                    : (m.sentTs ? fmtDuration(now - m.sentTs) : '…')}
                </span>
              </div>
            )}
            {/* Messages QUEUED (en attente derrière la réponse en cours) */}
            {m.role === 'assistant' && m.queued && (
              <div style={{ marginTop: 4, padding: '3px 8px', border: '1px dashed #fbbf24', borderRadius: 4, fontSize: 11, color: '#fbbf24', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>⏳ {m.content}</span>
                <button
                  onClick={() => {
                    setQueue((q) => q.filter((t) => t !== m.content));
                    setMessages((prev) => prev.filter((x) => !(x.queued && x.content === m.content)));
                  }}
                  title="Retirer de la file"
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#f87171', fontSize: 13 }}>🗑</button>
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
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) submit(); }}
          placeholder={ctx.t('panels.communication-dev.chat.placeholder') ?? 'Décrivez une tâche…'}
          style={{ flex: 1, fontSize: 12, padding: '5px 8px', background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6 }}
        />
        <button data-testid="dev-chat-send" onClick={submit} disabled={!input.trim()}
          style={{ padding: '5px 14px', fontSize: 12, cursor: 'pointer', background: busy ? '#7c3aed' : 'var(--mw-accent, #3b82f6)', color: '#fff', border: 'none', borderRadius: 6 }}
          title={busy ? 'Met le message en file d’attente' : 'Envoyer'}>
          {busy ? '⏳ File' : (ctx.t('panels.communication-dev-chat.envoyer') ?? 'Envoyer')}
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
