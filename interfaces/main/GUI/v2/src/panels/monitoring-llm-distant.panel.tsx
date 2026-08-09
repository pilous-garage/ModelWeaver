// monitoring/llm-distant — moniteur LLM : santé + usage + derniers LLM utilisés.
// Sources : monitoring/metrics (santé système + provider_metrics),
//           usage/monitor (fenêtres 1h/24h/7j),
//           monitoring/metrics → recent_llm (model_call_log réel).

import React, { useEffect, useRef, useState } from 'react';
import type { PanelDef } from './contract.ts';

const LANG_FR = `
panels:
  monitoring-llm-distant:
    titre: "Moniteur LLM"
    fenetre: "Fenêtre"
    requetes: "Requêtes"
    tokensIn: "Tokens in"
    tokensOut: "Tokens out"
    cout: "Coût"
    erreur: "Erreur"
    sante: "Santé"
    usage: "Consommation"
    recents: "Derniers LLM utilisés"
    latence: "Latence"
    dispo: "Disponibilité"
    dernierCall: "Dernier call"
    appelant: "Appelant"
    tous: "Tous"
`;

const LANG_EN = `
panels:
  monitoring-llm-distant:
    titre: "LLM monitor"
    fenetre: "Window"
    requetes: "Requests"
    tokensIn: "Tokens in"
    tokensOut: "Tokens out"
    cout: "Cost"
    erreur: "Error"
    sante: "Health"
    usage: "Usage"
    recents: "Last LLMs used"
    latence: "Latency"
    dispo: "Availability"
    dernierCall: "Last call"
    appelant: "Caller"
    tous: "All"
`;


const WINDOWS = ['5m', '15m', '1h', '4h', '24h', '7j'];

function LlmMonitorPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [metrics, setMetrics] = useState<any>(null);
  const [window, setWindow] = useState(params.window ?? '24h');
  const [caller, setCaller] = useState<string>('all');
  const [err, setErr] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const tick = async () => {
      try {
        const res = await ctx.api.post('monitoring/metrics', {});
        if (alive.current) { setMetrics(res?.result ?? {}); setErr(null); }
      } catch (e: any) {
        if (alive.current) setErr(String(e?.message ?? e));
      }
    };
    tick();
    const iv = setInterval(tick, 15000);
    return () => { alive.current = false; clearInterval(iv); };
  }, [ctx.api.post]);

  const usageSummary = metrics?.usage_summary ?? {};
  const w = usageSummary?.windows?.[window];
  const summary = w?.summary ?? {};
  const recents = metrics?.recent_llm?.calls ?? [];
  const sysStatus = metrics?.system_status ?? {};
  const callers = Array.from(new Set(recents.map((r: any) => r.caller_id ?? '?')));
  const shown = caller === 'all' ? recents : recents.filter((r: any) => (r.caller_id ?? '?') === caller);

  const fmt = (n: number | null | undefined) => n == null ? '0' : Number(n).toLocaleString('fr-FR');
  const fmtTime = (ts: number | null | undefined) => {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleTimeString('fr-FR');
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {err && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.monitoring-llm-distant.erreur') ?? 'Erreur'} : {err}</div>}

      {/* ── Santé système ── */}
      <div style={{ fontWeight: 700, margin: '2px 0 4px', color: '#a5b4fc' }}>{ctx.t?.('panels.monitoring-llm-distant.sante') ?? 'Santé'}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 4, marginBottom: 8, fontSize: 11 }}>
        <div>MW <b>{sysStatus.version ?? '?'}</b></div>
        <div style={{ color: sysStatus.daemon?.token_present ? '#4ade80' : '#f87171' }}>daemon {sysStatus.daemon?.token_present ? '✓' : '✕'}</div>
        <div style={{ color: sysStatus.usage_collector?.pidfile_present ? '#4ade80' : '#f87171' }}>usage {sysStatus.usage_collector?.pidfile_present ? '✓' : '✕'}</div>
      </div>

      {/* ── Consommation par fenêtre ── */}
      <div style={{ fontWeight: 700, margin: '4px 0 4px', color: '#67e8f9' }}>{ctx.t?.('panels.monitoring-llm-distant.usage') ?? 'Consommation'}</div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
        {WINDOWS.map((wd) => (
          <button
            key={wd}
            onClick={() => setWindow(wd)}
            className="mw-btn"
            style={{ padding: '2px 8px', fontSize: 11, opacity: wd === window ? 1 : 0.6 }}
          >{wd}</button>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginBottom: 4, fontSize: 11 }}>
        <div>{ctx.t?.('panels.monitoring-llm-distant.requetes') ?? 'Requêtes'} : {fmt(summary.requests)}</div>
        <div>{ctx.t?.('panels.monitoring-llm-distant.tokensIn') ?? 'Tokens in'} : {fmt(summary.tokens_in)}</div>
        <div>{ctx.t?.('panels.monitoring-llm-distant.tokensOut') ?? 'Tokens out'} : {fmt(summary.tokens_out)}</div>
        <div>{ctx.t?.('panels.monitoring-llm-distant.cout') ?? 'Coût'} : {Number(summary.cost ?? 0).toFixed(4)} $</div>
        <div style={{ gridColumn: '1 / -1', color: '#a5b4fc' }}>
          {ctx.t?.('panels.monitoring-llm-distant.dernierCall') ?? 'Dernier call'} : {fmtTime(summary.last_call)}
        </div>
      </div>

      {/* ── Derniers LLM utilisés (model_call_log réel) ── */}
      <div style={{ fontWeight: 700, margin: '10px 0 4px', color: '#4ade80' }}>{ctx.t?.('panels.monitoring-llm-distant.recents') ?? 'Derniers LLM utilisés'}</div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
        <button
          className="mw-btn"
          style={{ padding: '1px 8px', fontSize: 10, opacity: caller === 'all' ? 1 : 0.5 }}
          onClick={() => setCaller('all')}
        >{ctx.t?.('panels.monitoring-llm-distant.tous') ?? 'Tous'}</button>
        {callers.map((c: any) => (
          <button
            key={c}
            className="mw-btn"
            style={{ padding: '1px 8px', fontSize: 10, opacity: caller === c ? 1 : 0.5 }}
            onClick={() => setCaller(c)}
          >{c}</button>
        ))}
      </div>
      {shown.length === 0 && <div style={{ fontSize: 11, color: '#475569' }}>Aucun appel enregistré (24h)</div>}
      {shown.map((r: any, i: number) => {
        const rateLimited = (r.error_codes ?? []).some((c: string) => c?.toLowerCase().includes('rate_limit'));
        const down = (r.error_rate ?? 0) >= 50;
        return (
          <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span style={{ flex: 1, fontWeight: 600 }}>{r.model}</span>
              <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, color: '#0f172a', background: rateLimited ? '#fbbf24' : (down ? '#f87171' : '#34d399') }}>
                {rateLimited ? 'rate-limit' : (down ? '↓' : '✓')}
              </span>
            </div>
            <div style={{ fontSize: 11, color: '#94a3b8', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <span>{r.provider}</span>
              <span style={{ color: '#c084fc' }}>{ctx.t?.('panels.monitoring-llm-distant.appelant') ?? 'Appelant'} : {r.caller_id}</span>
              <span>{r.requests} req</span>
              <span>in {fmt(r.tokens_in)}</span>
              <span>out {fmt(r.tokens_out)}</span>
              {r.latency_ms != null && <span>{ctx.t?.('panels.monitoring-llm-distant.latence') ?? 'Lat'}. {r.latency_ms}ms</span>}
              {r.errors > 0 && <span style={{ color: '#f87171' }}>{r.errors} erreurs ({r.error_rate}%)</span>}
              <span style={{ color: '#a5b4fc' }}>{fmtTime(r.last_call)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'monitoring-llm-distant',
  labelKey: 'panels.monitoring-llm-distant.titre',
  iconKey: 'panels.monitoring-llm-distant.titre',
  version: '1.2.0',
  essential: false,
  bundles: ['monitoring'],
  paramsSchema: {
    window: { type: 'enum', enum: ['5m', '15m', '1h', '4h', '24h', '7j'], default: '24h' },
  },
  defaultParams: { window: '24h' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[monitoring-llm-distant] Moniteur LLM v1.1.0\n  santé (monitoring/metrics) + usage (usage/monitor) + derniers LLM (recent_llm)',
  component: LlmMonitorPanel,
};

export const langFr = LANG_FR;