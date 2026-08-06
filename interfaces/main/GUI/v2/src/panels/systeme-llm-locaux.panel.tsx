// systeme/llm-locaux — moteurs LLM locaux (Ollama, llama.cpp, LM Studio).
// Routes : llm/local/list, llm/local/start|stop {engine, hardware},
//          llm/local/start-model, llm/hf/local (GGUF locaux),
//          llm/hf/search, llm/hf/download, llm/chat (test).

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  systeme-llm-locaux:
    titre: "LLM locaux"
    debut: "Démarrer (CPU)"
    arreter: "Arrêter"
    modeles: "Modèles GGUF locaux"
    charger: "Charger (CPU)"
    test: "Test du modèle"
    envoyer: "Envoyer"
    reponse: "Réponse"
    modelf: "Nom du modèle (ex. smollm2:135m)"
    aucun: "Aucun modèle GGUF local"
`;

const LANG_EN = `
panels:
  systeme-llm-locaux:
    titre: "Local LLMs"
    debut: "Start (CPU)"
    arreter: "Stop"
    modeles: "Local GGUF models"
    charger: "Load (CPU)"
    test: "Test chat"
    envoyer: "Send"
    reponse: "Reply"
    modelf: "Model name (e.g. smollm2:135m)"
    aucun: "No local GGUF"
`;


function LlmLocauxPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data: list, error: listErr } = usePoll<any>(
    ctx.api.post, 'llm/local/list', {}, 5000,
    (res) => res?.result ?? res ?? {},
  );
  const { data: localModels, reload: reloadLocal } = usePoll<any>(
    ctx.api.post, 'llm/hf/local', {}, 10000,
    (res) => res?.result?.models ?? res?.models ?? [],
    true,
  );
  const engineList = list?.engines ?? [];
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [modelName, setModelName] = React.useState('smollm2:135m');
  const [testPrompt, setTestPrompt] = React.useState('Dis bonjour en français.');
  const [testReply, setTestReply] = React.useState<{ model: string; content: string } | null>(null);
  const [testBusy, setTestBusy] = React.useState(false);

  const act = async (route: string, body: any, label: string) => {
    setBusy(true);
    try {
      const res = await ctx.api.post(route, body);
      const r = res?.result ?? res ?? {};
      if (r?.status === 'error' || r?.ok === false) {
        setMsg({ ok: false, text: `${label} : ${r?.error ?? JSON.stringify(r)}` });
      } else {
        setMsg({ ok: true, text: `${label} : ok` });
      }
    } catch (e: any) {
      setMsg({ ok: false, text: `${label} : ${String(e?.message ?? e)}` });
    }
    setBusy(false);
    reloadLocal();
  };

  const testChat = async () => {
    setTestBusy(true);
    setTestReply(null);
    try {
      const res = await ctx.api.post('llm/chat', {
        provider_ref: 'ollama',
        model_ref: modelName || 'smollm2:135m',
        messages: [{ role: 'user', content: testPrompt || 'Dis bonjour.' }],
        temperature: 0.2,
        max_tokens: 200,
      });
      const r = res?.result ?? res ?? {};
      if (r?.status === 'error') {
        setTestReply({ model: modelName, content: `✕ ${r.error}` });
      } else {
        setTestReply({ model: r?.model ?? modelName, content: r?.content ?? '(vide)' });
      }
    } catch (e: any) {
      setTestReply({ model: modelName, content: `✕ ${String(e?.message ?? e)}` });
    }
    setTestBusy(false);
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {msg ? <div style={{ fontSize: 11, padding: '4px 8px', borderRadius: 6, marginBottom: 6, background: msg.ok ? '#052e16' : '#450a0a', color: msg.ok ? '#4ade80' : '#f87171' }}>{msg.text}</div> : null}
      {listErr ? <div style={{ fontSize: 11, color: '#f87171', marginBottom: 6 }}>{listErr}</div> : null}

      <div style={{ fontWeight: 700, margin: '2px 0 4px', color: '#a5b4fc' }}>Moteurs</div>
      {engineList.length === 0 && !listErr && <div style={{ color: '#64748b' }}>Aucun moteur détecté</div>}
      {engineList.map((e: any) => {
        const label = e.name ?? e.ref;
        return (
          <div key={e.ref ?? e.name} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
            <span style={{ flex: 1, fontWeight: 600 }}>{label}</span>
            <span style={{ color: e.running ? '#4ade80' : '#94a3b8' }}>{e.running ? '●' : '○'}</span>
            <span style={{ color: '#64748b' }}>{e.port ?? ''}</span>
            {e.running ? (
              <button className="mw-btn" disabled={busy} onClick={() => act('llm/local/stop', { engine: e.ref }, `Arrêt ${label}`)} style={{ fontSize: 11 }}>■ {(ctx.t?.('panels.systeme-llm-locaux.arreter') ?? 'Arrêter')}</button>
            ) : (
              <button className="mw-btn" disabled={busy} onClick={() => act('llm/local/start', { engine: e.ref, hardware: 'cpu' }, `Démarrage ${label}`)} style={{ fontSize: 11 }}>▶ {(ctx.t?.('panels.systeme-llm-locaux.debut') ?? 'Démarrer (CPU)')}</button>
            )}
          </div>
        );
      })}

      <div style={{ fontWeight: 700, margin: '10px 0 4px', color: '#67e8f9' }}>{ctx.t?.('panels.systeme-llm-locaux.modeles') ?? 'Modèles GGUF locaux'}</div>
      {(localModels ?? []).length === 0 && <div style={{ fontSize: 11, color: '#475569' }}>{ctx.t?.('panels.systeme-llm-locaux.aucun') ?? 'Aucun modèle GGUF local'}</div>}
      {(localModels ?? []).map((m: any, i: number) => {
        const stem = (m.filename || '').replace(/\.gguf$/i, '');
        return (
          <div key={m.path ?? i} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
            <span style={{ flex: 1, fontWeight: 600 }}>{stem}</span>
            <span style={{ color: '#64748b', fontSize: 11 }}>{m.size_gb != null ? `${m.size_gb} GB` : ''}</span>
            <button className="mw-btn" disabled={busy} onClick={() => act('llm/local/start-model', { engine: 'llamacpp', model: `local:${stem}`, hardware: 'cpu' }, 'Charger GGUF')} style={{ fontSize: 11 }}>{ctx.t?.('panels.systeme-llm-locaux.charger') ?? 'Charger (CPU)'}</button>
          </div>
        );
      })}

      <div style={{ fontWeight: 700, margin: '10px 0 4px', color: '#4ade80' }}>{ctx.t?.('panels.systeme-llm-locaux.test') ?? 'Test du modèle'}</div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
        <input value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder={ctx.t?.('panels.systeme-llm-locaux.modelf') ?? 'Nom du modèle'}
          style={{ flex: 1, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '4px 8px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 12 }} />
        <button className="mw-btn" disabled={testBusy} onClick={testChat} style={{ fontSize: 11 }}>{ctx.t?.('panels.systeme-llm-locaux.envoyer') ?? 'Envoyer'}</button>
      </div>
      <textarea value={testPrompt} onChange={(e) => setTestPrompt(e.target.value)}
        style={{ width: '100%', background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '4px 8px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 11, minHeight: 40, resize: 'vertical', boxSizing: 'border-box' }} />
      {testBusy && <div style={{ fontSize: 11, color: '#94a3b8', margin: '6px 0' }}>…</div>}
      {testReply ? (
        <div style={{ margin: '6px 0', fontSize: 11, whiteSpace: 'pre-wrap', background: 'rgba(148,163,184,0.08)', padding: 6, borderRadius: 6, border: '1px solid var(--mw-border, #334155)' }}>
          <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>{testReply.model}</div>
          {testReply.content}
        </div>
      ) : null}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'systeme-llm-locaux',
  labelKey: 'panels.systeme-llm-locaux.titre',
  iconKey: 'panels.systeme-llm-locaux.titre',
  version: '1.1.0',
  essential: false,
  bundles: ['systeme'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[systeme-llm-locaux] LLM locaux v1.1.0\n  start/stop moteur (CPU), GGUF locaux, test chat ollama\n  routes: llm/local/list, start, stop, start-model, hf/local, llm/chat',
  component: LlmLocauxPanel,
};

export const langFr = LANG_FR;