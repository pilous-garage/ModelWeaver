import React, { useEffect, useRef, useState } from 'react';

/**
 * CustomSelect — remplace le <select> natif dont l'option sélectionnée est
 * illisible sur les thèmes sombres (fond/texte trop proches) et dont le menu
 * natif est trop petit (ex. fournisseur opencode avec beaucoup de modèles).
 *
 * - Champ fermé : texte `--mw-fg` sur `--mw-bg-panel` (lisible).
 * - Menu ouvert : large (jusqu'à 360px), hauteur max avec scroll.
 * - Clic extérieur / Échap : ferme.
 */
export function CustomSelect(props: {
  value: string;
  onChange(v: string): void;
  options: { value: string; label: string; hint?: string }[];
  placeholder?: string;
  disabled?: boolean;
  testid?: string;
  maxWidth?: number;
}) {
  const { value, onChange, options, placeholder, disabled, testid, maxWidth = 220 } = props;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);

  const selected = options.find((o) => o.value === value);

  const base = {
    fontSize: 11, padding: '2px 6px', background: 'var(--mw-bg, #0f172a)',
    color: 'var(--mw-fg, #e2e8f0)', border: '1px solid var(--mw-border, #334155)',
    borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap' as const, userSelect: 'none' as const,
  };

  return (
    <div ref={rootRef} style={{ position: 'relative', display: 'inline-block' }}>
      <div
        data-testid={testid}
        onClick={() => { if (!disabled) setOpen((v) => !v); }}
        style={{
          ...base,
          opacity: disabled ? 0.5 : 1,
          pointerEvents: disabled ? 'none' : 'auto',
          display: 'flex', alignItems: 'center', gap: 4,
          maxWidth,
          overflow: 'hidden', textOverflow: 'ellipsis',
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', flex: 1 }}>
          {selected ? selected.label : (placeholder ?? '—')}
        </span>
        <span style={{ color: '#64748b', fontSize: 9 }}>▼</span>
      </div>

      {open && (
        <div
          data-testid={`${testid}-menu`}
          style={{
            position: 'absolute', top: '100%', left: 0, zIndex: 1000, marginTop: 2,
            background: 'var(--mw-bg-panel, #1e293b)', border: '1px solid var(--mw-border, #334155)',
            borderRadius: 6, boxShadow: '0 6px 20px rgba(0,0,0,.4)',
            minWidth: 180, maxWidth: 360, maxHeight: 320, overflow: 'auto',
          }}
        >
          {options.map((o) => (
            <div
              key={o.value}
              data-testid={`${testid}-opt-${o.value}`}
              onClick={() => { onChange(o.value); setOpen(false); }}
              style={{
                padding: '3px 8px', fontSize: 11, cursor: 'pointer',
                color: o.value === value ? 'var(--mw-accent, #3b82f6)' : 'var(--mw-fg, #e2e8f0)',
                background: o.value === value ? 'var(--mw-accent-bg, #164e63)' : 'transparent',
                display: 'flex', gap: 6, alignItems: 'center',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'var(--mw-bg-hover, #243349)'; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = o.value === value ? 'var(--mw-accent-bg, #164e63)' : 'transparent'; }}
            >
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.label}</span>
              {o.hint && <span style={{ color: '#64748b', fontSize: 10, flexShrink: 0 }}>{o.hint}</span>}
            </div>
          ))}
          {options.length === 0 && (
            <div style={{ padding: '6px 8px', color: '#64748b', fontSize: 11 }}>—</div>
          )}
        </div>
      )}
    </div>
  );
}
