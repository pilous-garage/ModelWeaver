import React from 'react';

/**
 * Triple-checkbox : trois états cycliques.
 *   - 'off'   : vide (neutre — on s'en fout)
 *   - 'yes'   : vert + (je VEUX ce flag : ne garder que ceux qui l'ont)
 *   - 'no'    : rouge − (je NE veux PAS ce flag : exclure ceux qui l'ont)
 * Clic = cycle off → yes → no → off.
 */
export type TriState = 'off' | 'yes' | 'no';

export function nextTri(state: TriState): TriState {
  if (state === 'off') return 'yes';
  if (state === 'yes') return 'no';
  return 'off';
}

export function TripleCheckbox(props: {
  value: TriState;
  onChange(v: TriState): void;
  title?: string;
  testid?: string;
}) {
  const { value, onChange, title, testid } = props;
  const color = value === 'yes' ? '#4ade80' : value === 'no' ? '#f87171' : '#64748b';
  const glyph = value === 'yes' ? '+' : value === 'no' ? '−' : '';
  return (
    <span
      data-testid={testid}
      title={title}
      onClick={(e) => { e.stopPropagation(); onChange(nextTri(value)); }}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 14, height: 14, borderRadius: 3, cursor: 'pointer',
        border: `1px solid ${color}`, color, fontSize: 11, lineHeight: 1,
        userSelect: 'none',
      }}
    >{glyph}</span>
  );
}
