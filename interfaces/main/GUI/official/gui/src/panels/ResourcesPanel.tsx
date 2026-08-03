import React, { useEffect, useState } from 'react';
import type { AppApi } from '../useApp.ts';
import { UsageBar } from '../components/ui.tsx';
import { daemonPost } from '../bridge.ts';

function fmtBps(bps: number | null | undefined): string {
  if (bps == null) return '—';
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(2)} MB/s`;
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(1)} kB/s`;
  return `${bps.toFixed(0)} B/s`;
}

export function ResourcesPanel({ app }: { app: AppApi }) {
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const d = await daemonPost('system/resources', {});
        if (alive) { setRes(d?.result || null); setErr(''); }
      } catch (e: any) {
        if (alive) setErr(e.message || 'daemon indisponible');
      }
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(t); };
  }, []);

   const gpus = res?.gpus || [];
   const ifaces = (res?.network?.interfaces || []).filter((i: any) => i.rx_bps != null || i.tx_bps != null);

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.2rem' }}>Ressources consommées (temps réel)</h3>
      <div style={{ fontSize: '0.62rem', color: '#475569', marginBottom: '0.7rem', fontFamily: 'monospace' }}>machine globale · maj 2s</div>

      {app.systemState ? (
        <div>
          <UsageBar
            pct={app.systemState.cpu_percent}
            label={`CPU (${app.systemState.cpu_count ?? '?'} cœurs)`}
          />
          <UsageBar
            pct={app.systemState.ram_total_gb ? (app.systemState.ram_used_gb ?? (app.systemState.ram_total_gb - (app.systemState.ram_available_gb ?? 0))) / app.systemState.ram_total_gb * 100 : null}
            label="RAM"
            detail={app.systemState.ram_used_gb != null ? `${app.fmtGb(app.systemState.ram_used_gb)} / ${app.fmtGb(app.systemState.ram_total_gb)}` : undefined}
          />
          <UsageBar
            pct={app.systemState.disk_total_gb ? (app.systemState.disk_used_gb ?? (app.systemState.disk_total_gb - (app.systemState.disk_free_gb ?? 0))) / app.systemState.disk_total_gb * 100 : null}
            label="Disque"
            detail={app.systemState.disk_used_gb != null ? `${app.fmtGb(app.systemState.disk_used_gb)} / ${app.fmtGb(app.systemState.disk_total_gb)}` : undefined}
          />
        </div>
      ) : <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Chargement…</div>}

      {gpus.length > 0 && (
        <div style={{ marginTop: '0.7rem' }}>
          <div style={{ fontSize: '0.66rem', color: '#64748b', marginBottom: '0.35rem' }}>GPU</div>
          {gpus.map((g: any, i: number) => (
            <UsageBar key={i} pct={g.busy_percent} label={g.name || `GPU ${i + 1}`}
              detail={g.temp_c != null ? `${g.temp_c}°C` : undefined} />
          ))}
        </div>
       )}

       {ifaces.length > 0 && (
        <div style={{ marginTop: '0.7rem' }}>
          <div style={{ fontSize: '0.66rem', color: '#64748b', marginBottom: '0.35rem' }}>Bande passante (↓ rx / ↑ tx)</div>
          {ifaces.map((n: any, i: number) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', lineHeight: '1.7', borderBottom: '1px solid #1e293b' }}>
              <span style={{ color: '#94a3b8' }}>{n.name}</span>
              <span style={{ fontFamily: 'monospace' }}>
                <span style={{ color: '#6ee7b7' }}>↓ {fmtBps(n.rx_bps)}</span>
                <span style={{ color: '#64748b', margin: '0 0.3rem' }}>·</span>
                <span style={{ color: '#93c5fd' }}>↑ {fmtBps(n.tx_bps)}</span>
              </span>
            </div>
          ))}
        </div>
      )}

      {err && <div style={{ color: '#fca5a5', fontSize: '0.7rem', marginTop: '0.5rem' }}>{err}</div>}
    </div>
  );
}
