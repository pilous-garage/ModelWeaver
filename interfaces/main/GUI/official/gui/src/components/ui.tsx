import React from 'react';

// Global styles for select dropdowns
export const selectStyles: React.CSSProperties = {
  backgroundColor: '#1e293b',
  color: '#e2e8f0',
  border: '1px solid #475569',
  borderRadius: '0.25rem',
  fontSize: '0.75rem',
  outline: 'none',
  boxShadow: '0 0 0 1px rgba(59, 130, 246, 0.5)',
  appearance: 'none',
  WebkitAppearance: 'none',
  MozAppearance: 'none',
};

export function Spinner({ size = 12, color = '#e2e8f0' }: { size?: number; color?: string }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: size, height: size,
        border: `2px solid ${color}`,
        borderTopColor: 'transparent',
        borderRadius: '50%',
        animation: 'mw-spin 0.7s linear infinite',
        verticalAlign: 'middle',
      }}
    />
  );
}

// Bouton de signal (agents)
export function sigBtn(bg: string): React.CSSProperties {
  return {
    padding: '0.25rem 0.5rem', fontSize: '0.68rem', fontWeight: 600,
    borderRadius: '0.3rem', border: 'none', cursor: 'pointer', backgroundColor: bg, color: 'white',
  };
}

// Barre de consommation (0-100%), couleur selon seuil.
export function UsageBar({ pct, label, detail }: { pct: number | null | undefined; label: string; detail?: string }) {
  const v = (pct == null || isNaN(pct as number)) ? 0 : Math.max(0, Math.min(100, pct as number));
  const color = v > 90 ? '#ef4444' : v > 70 ? '#f59e0b' : '#22c55e';
  return (
    <div style={{ marginBottom: '0.55rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', marginBottom: '0.2rem' }}>
        <span style={{ color: '#94a3b8' }}>{label}</span>
        <span style={{ fontFamily: 'monospace', color: '#e2e8f0' }}>
          {pct == null || isNaN(pct as number) ? 'n/a' : `${Math.round(pct as number)}%`}{detail ? ` · ${detail}` : ''}
        </span>
      </div>
      <div style={{ height: '0.5rem', backgroundColor: '#0f172a', borderRadius: '0.25rem', overflow: 'hidden', border: '1px solid #334155' }}>
        <div style={{ width: `${v}%`, height: '100%', backgroundColor: color, transition: 'width 0.4s ease' }} />
      </div>
    </div>
  );
}
