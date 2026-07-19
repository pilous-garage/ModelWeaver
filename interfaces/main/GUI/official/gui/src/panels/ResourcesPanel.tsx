import React from 'react';
import type { AppApi } from '../useApp.ts';
import { UsageBar } from '../components/ui.tsx';

export function ResourcesPanel({ app }: { app: AppApi }) {
  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.2rem' }}>Ressources consommées (temps réel)</h3>
      <div style={{ fontSize: '0.62rem', color: '#475569', marginBottom: '0.7rem', fontFamily: 'monospace' }}>machine globale · maj 1.5s</div>
      {app.systemState ? (
        <div>
          <UsageBar
            pct={app.systemState.cpu_percent}
            label={`CPU (${app.systemState.cpu_count ?? '?' } cœurs)`}
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
          {app.systemState.cpu_per_core && app.systemState.cpu_per_core.length > 0 && (
            <div style={{ marginTop: '0.6rem' }}>
              <div style={{ fontSize: '0.66rem', color: '#64748b', marginBottom: '0.35rem' }}>Par cœur</div>
              <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                {app.systemState.cpu_per_core.map((c: number, i: number) => {
                  const cv = c == null ? 0 : Math.max(0, Math.min(100, c));
                  const cc = cv > 90 ? '#ef4444' : cv > 70 ? '#f59e0b' : '#22c55e';
                  return (
                    <div key={i} title={`cœur ${i} : ${Math.round(cv)}%`}
                      style={{ flex: '1 1 0', minWidth: '8px', height: '2.2rem', backgroundColor: '#0f172a', borderRadius: '0.2rem', border: '1px solid #334155', display: 'flex', alignItems: 'flex-end', overflow: 'hidden' }}>
                      <div style={{ width: '100%', height: `${cv}%`, backgroundColor: cc, transition: 'height 0.4s ease' }} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      ) : <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>…</div>}
    </div>
  );
}
