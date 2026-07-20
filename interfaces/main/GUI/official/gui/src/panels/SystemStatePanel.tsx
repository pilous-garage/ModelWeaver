import React from 'react';
import type { AppApi } from '../useApp.ts';

export function SystemStatePanel({ app }: { app: AppApi }) {
  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.75rem' }}>État du système</h3>
      {app.systemState ? (
        <div style={{ fontSize: '0.78rem', lineHeight: '1.7' }}>
          <div><span style={{ color: '#64748b' }}>OS :</span> {app.systemState.os} {app.systemState.os_version || ''}</div>
          <div><span style={{ color: '#64748b' }}>Arch :</span> {app.systemState.architecture}</div>
          <div><span style={{ color: '#64748b' }}>Cœurs :</span> {app.systemState.cpu_count ?? 'n/a'}</div>
          <div><span style={{ color: '#64748b' }}>RAM :</span> {app.fmtGb(app.systemState.ram_total_gb)} <span style={{ color: '#64748b' }}>(libre {app.fmtGb(app.systemState.ram_available_gb)})</span></div>
          <div><span style={{ color: '#64748b' }}>Disque :</span> {app.fmtGb(app.systemState.disk_total_gb)} <span style={{ color: '#64748b' }}>(libre {app.fmtGb(app.systemState.disk_free_gb)})</span></div>
          <div style={{ marginTop: '0.5rem' }}>
            <span style={{ color: '#64748b' }}>Gestionnaires :</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', marginTop: '0.3rem' }}>
              {(app.systemState.detected_managers || []).map((pm: string) => (
                <span key={pm} style={{ fontSize: '0.65rem', backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '0.25rem', padding: '0.1rem 0.4rem', color: '#6ee7b7' }}>{pm}</span>
              ))}
              {(app.systemState.detected_managers || []).length === 0 && <span style={{ color: '#fca5a5' }}>aucun</span>}
            </div>
          </div>
        </div>
      ) : <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Chargement…</div>}
    </div>
  );
}
