import React, { useCallback, useEffect, useRef, useState } from 'react';
import { nextZoom, prevZoom, zoomToPct, pctToZoom } from '../zoom.ts';

/**
 * ZoomBar — contrôles de zoom d'un niveau (global, panel ou mini-layout) :
 * cadenas 🔒/🔓, valeur en %, boutons −/+, clic sur la valeur pour l'éditer
 * librement (au-delà des bornes si besoin). La graduation +/− suit les paliers
 * (zoom.ts) ; l'édition manuelle accepte n'importe quelle valeur.
 */
export function ZoomBar(props: {
  value: number; // zoom EFFECTIF (×)
  locked?: boolean;
  onChange(value: number): void; // effective (×)
  onToggleLock?(): void;
  testidPrefix?: string;
}) {
  const { value, locked, onChange, onToggleLock, testidPrefix = '' } = props;
  const pct = zoomToPct(value);
  const [editing, setEditing] = useState(false);
  const [editVal, setEditVal] = useState<string>('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const commit = useCallback(() => {
    setEditing(false);
    const n = parseFloat(editVal.replace('%', '').replace(',', '.'));
    if (!Number.isNaN(n) && n > 0) onChange(pctToZoom(n));
  }, [editVal, onChange]);

  const style = {
    display: 'flex', alignItems: 'center', gap: 2, fontSize: 11,
    color: '#94a3b8', whiteSpace: 'nowrap' as const, userSelect: 'none' as const,
  };
  const btn = (title: string, glyph: string, data: string, fn: () => void) => (
    <span
      data-testid={data}
      onClick={(e) => { e.stopPropagation(); fn(); }}
      title={title}
      style={{ cursor: 'pointer', padding: '0 4px', borderRadius: 3, color: '#94a3b8', fontSize: 12, lineHeight: 1 }}
      onMouseDown={(e) => e.stopPropagation()}
    >{glyph}</span>
  );

  return (
    <div style={style} className="mw-zoom-bar" data-testid={`${testidPrefix}zoom-bar`}>
      {btn(locked ? 'Zoom verrouillé' : 'Verrouiller le zoom', locked ? '🔒' : '🔓', `${testidPrefix}zoom-lock`, () => onToggleLock?.())}
      {editing ? (
        <input
          ref={inputRef}
          data-testid={`${testidPrefix}zoom-input`}
          value={editVal}
          onChange={(e) => setEditVal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            else if (e.key === 'Escape') setEditing(false);
          }}
          onBlur={commit}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          style={{
            width: 52, fontSize: 11, padding: '0 4px', textAlign: 'right',
            background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)',
            border: '1px solid var(--mw-accent, #3b82f6)', borderRadius: 3, outline: 'none',
          }}
        />
      ) : (
        <span
          data-testid={`${testidPrefix}zoom-value`}
          onDoubleClick={(e) => { e.stopPropagation(); setEditVal(String(pct)); setEditing(true); }}
          title="Double-clic pour éditer"
          style={{ cursor: 'text', minWidth: 38, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
        >{pct}%</span>
      )}
      {btn('Zoom −', '−', `${testidPrefix}zoom-minus`, () => onChange(prevZoom(value)))}
      {btn('Zoom +', '+', `${testidPrefix}zoom-plus`, () => onChange(nextZoom(value)))}
    </div>
  );
}
