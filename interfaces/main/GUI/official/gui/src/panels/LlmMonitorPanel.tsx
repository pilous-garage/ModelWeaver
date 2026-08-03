import React, { useEffect, useState } from 'react';
import { daemonPost } from '../bridge.ts';

const WINDOWS = [
  { key: '1h', label: '1 h' },
  { key: '24h', label: '24 h' },
  { key: '7j', label: '7 j' },
];

function fmtCost(c: number): string {
  if (c == null || c === 0) return '0,00 $';
  if (c < 0.01) return `${(c * 1000).toFixed(2)} m$`;
  return `${c.toFixed(2)} $`;
}

function fmtTok(n: number): string {
  if (n == null) return '0';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} k`;
  return String(n);
}

export function LlmMonitorPanel() {
  const [data, setData] = useState<any>(null);
  const [window, setWindow] = useState('1h');
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const d = await daemonPost('usage/monitor', {});
        if (alive) { setData(d?.result?.windows || null); setErr(''); }
      } catch (e: any) {
        if (alive) setErr(e.message || 'daemon indisponible');
      }
    };
    poll();
    const t = setInterval(poll, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const w = data?.[window];

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.2rem' }}>
        <h3 style={{ fontSize: '0.9rem', fontWeight: '600', margin: 0 }}>Moniteur LLM distant</h3>
        <div style={{ display: 'flex', gap: '0.3rem' }}>
          {WINDOWS.map(win => (
            <button key={win.key} onClick={() => setWindow(win.key)}
              style={{
                fontSize: '0.65rem', padding: '0.15rem 0.5rem', borderRadius: '0.3rem',
                border: '1px solid #334155', cursor: 'pointer',
                backgroundColor: window === win.key ? '#3b82f6' : '#0f172a',
                color: window === win.key ? '#fff' : '#94a3b8',
              }}>{win.label}</button>
          ))}
        </div>
      </div>
      <div style={{ fontSize: '0.62rem', color: '#475569', marginBottom: '0.7rem', fontFamily: 'monospace' }}>
        tokens (entrée / sortie / raisonnement) · coût USD · maj 15s
      </div>

      {err && <div style={{ color: '#fca5a5', fontSize: '0.7rem', marginBottom: '0.5rem' }}>{err}</div>}
      {!w && !err ? (
        <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Chargement…</div>
      ) : w && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.4rem', marginBottom: '0.8rem' }}>
            {[
              { label: 'Requêtes', v: fmtTok(w.summary?.requests ?? 0), c: '#e2e8f0' },
              { label: 'Tokens in', v: fmtTok(w.summary?.tokens_in ?? 0), c: '#6ee7b7' },
              { label: 'Tokens out', v: fmtTok(w.summary?.tokens_out ?? 0), c: '#93c5fd' },
              { label: 'Coût', v: fmtCost(w.summary?.cost ?? 0), c: '#fbbf24' },
            ].map(s => (
              <div key={s.label} style={{ backgroundColor: '#0f172a', borderRadius: '0.35rem', border: '1px solid #334155', padding: '0.4rem' }}>
                <div style={{ fontSize: '0.6rem', color: '#64748b' }}>{s.label}</div>
                <div style={{ fontSize: '0.85rem', fontWeight: '600', color: s.c }}>{s.v}</div>
              </div>
            ))}
          </div>

          {(w.summary?.tokens_thinking ?? 0) > 0 && (
            <div style={{ fontSize: '0.68rem', color: '#c084fc', marginBottom: '0.6rem' }}>
              ⚙ tokens raisonnement : {fmtTok(w.summary.tokens_thinking)}
            </div>
          )}

          <div style={{ fontSize: '0.66rem', color: '#64748b', marginBottom: '0.3rem' }}>Par modèle</div>
          <div style={{ maxHeight: '10rem', overflow: 'auto' }}>
            {(w.models || []).map((m: any, i: number) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.68rem', lineHeight: '1.8', borderBottom: '1px solid #1e293b' }}>
                <span style={{ color: '#e2e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '45%' }}>
                  <span style={{ color: '#64748b', marginRight: '0.3rem' }}>{m.provider}</span>{m.model}
                </span>
                <span style={{ fontFamily: 'monospace', color: '#94a3b8' }}>
                  {fmtTok(m.requests)} req · {fmtTok(m.tokens_in)}/{fmtTok(m.tokens_out)}
                  {m.tokens_thinking ? `/${fmtTok(m.tokens_thinking)}` : ''}
                  <span style={{ color: '#fbbf24', marginLeft: '0.4rem' }}>{fmtCost(m.cost)}</span>
                </span>
              </div>
            ))}
            {(w.models || []).length === 0 && <div style={{ color: '#94a3b8', fontSize: '0.7rem' }}>aucun appel sur cette fenêtre</div>}
          </div>
        </div>
      )}
    </div>
  );
}
