/** MenuBar — Rendu d'un menu depuis un JSON */

import React, { useState, useRef, useEffect } from 'react';
import type { MenuItemDef } from './useLayout.ts';

interface Props {
  menu: MenuItemDef[];
  onAction: (action: string) => void;
}

export function MenuBar({ menu, onAction }: Props) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center',
      background: 'var(--bg-panel, #16213e)',
      borderBottom: '1px solid var(--border, #2a2a4a)',
      padding: '0 0.5rem',
      height: '32px',
      fontSize: '0.75rem',
      userSelect: 'none',
    }}>
      {menu.map((item, idx) => (
        <MenuItem key={item.id || idx} item={item} onAction={onAction} depth={0} />
      ))}
    </div>
  );
}

function MenuItem({ item, onAction, depth }: { item: MenuItemDef; onAction: (action: string) => void; depth: number }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handle = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [open]);

  if (item.type === 'separator') {
    return <div style={{ width: '1px', height: '16px', background: 'var(--border)', margin: '0 4px' }} />;
  }

  const hasChildren = item.items && item.items.length > 0;
  const isToggle = item.type === 'toggle-visibility';

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <div
        onClick={() => {
          if (hasChildren) setOpen(!open);
          else if (item.action) onAction(item.action);
        }}
        onMouseEnter={() => depth > 0 && setOpen(true)}
        style={{
          padding: '2px 8px',
          cursor: 'pointer',
          color: 'var(--fg, #e0e0e0)',
          background: open ? 'var(--accent, #0f3460)' : 'transparent',
          borderRadius: '3px',
          whiteSpace: 'nowrap',
          display: 'flex', alignItems: 'center', gap: '4px',
          opacity: item.disabled ? 0.5 : 1,
          pointerEvents: item.disabled ? 'none' : undefined,
        }}
      >
        {isToggle && (
          <span style={{ width: '12px', textAlign: 'center' }}>
            {item.checked ? '☑' : '☐'}
          </span>
        )}
        <span>{item.label}</span>
        {item.shortcut && (
          <span style={{ marginLeft: '12px', fontSize: '0.6rem', color: '#64748b' }}>{item.shortcut}</span>
        )}
        {hasChildren && <span style={{ marginLeft: '4px', fontSize: '0.6rem' }}>▶</span>}
      </div>
      {hasChildren && open && (
        <div style={{
          position: 'absolute', top: '100%', left: depth > 0 ? '100%' : 0,
          background: 'var(--bg-panel, #16213e)',
          border: '1px solid var(--border, #2a2a4a)',
          borderRadius: '4px',
          minWidth: '200px',
          zIndex: 1000,
          padding: '4px 0',
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        }}>
          {item.items!.map((sub, idx) => (
            <MenuItem key={sub.id || idx} item={sub} onAction={onAction} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
