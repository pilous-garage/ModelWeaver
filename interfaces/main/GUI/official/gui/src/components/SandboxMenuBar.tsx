import React, { useState, useRef, useEffect } from 'react';
import type { CatalogueType } from '../useSandbox.ts';

interface MenuItem {
  type?: 'item';
  label: string;
  action?: () => void;
  checked?: boolean;
  disabled?: boolean;
  children?: (MenuItem | MenuSeparator)[];
}
interface MenuSeparator { type: 'separator'; }
interface Menu { label: string; children: (MenuItem | MenuSeparator)[]; }

const TYPE_LABELS: Record<CatalogueType, string> = {
  skills: 'Skill',
  behaviors: 'Comportement',
  personalities: 'Personnalité',
  roles: 'Rôle',
  agents: 'Agent',
};

export interface SandboxMenuActions {
  onNew: (type: CatalogueType) => void;
  onSave: () => void;
  onSaveAll: () => void;
  onCloseTab: () => void;
  onRescanLib: () => void;
  showCatalogue: boolean;
  onToggleCatalogue: () => void;
  showLego: boolean;
  onToggleLego: () => void;
  hasActive: boolean;
}

export function SandboxMenuBar(a: SandboxMenuActions) {
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) setOpenMenu(null);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const close = () => setOpenMenu(null);

  const menus: Menu[] = [
    {
      label: 'Fichier',
      children: [
        {
          label: 'Nouveau',
          children: (Object.keys(TYPE_LABELS) as CatalogueType[]).map(t => ({
            type: 'item' as const,
            label: TYPE_LABELS[t],
            action: () => { a.onNew(t); close(); },
          })),
        },
        { type: 'separator' },
        { type: 'item', label: 'Enregistrer', action: () => { a.onSave(); close(); }, disabled: !a.hasActive },
        { type: 'item', label: 'Enregistrer tout', action: () => { a.onSaveAll(); close(); } },
        { type: 'separator' },
        { type: 'item', label: "Fermer l'onglet", action: () => { a.onCloseTab(); close(); }, disabled: !a.hasActive },
      ],
    },
    {
      label: 'Affichage',
      children: [
        { type: 'item', label: 'Panneau catalogue', checked: a.showCatalogue, action: () => { a.onToggleCatalogue(); close(); } },
        { type: 'item', label: 'Panneau lego', checked: a.showLego, action: () => { a.onToggleLego(); close(); } },
      ],
    },
    {
      label: 'Outils',
      children: [
        { type: 'item', label: '🔄 Rescanner la librairie', action: () => { a.onRescanLib(); close(); } },
      ],
    },
  ];

  return (
    <div
      ref={barRef}
      style={{
        display: 'flex', alignItems: 'center', padding: '0 0.5rem',
        backgroundColor: '#181825', borderBottom: '1px solid #313244',
        flexShrink: 0, height: '1.8rem',
      }}
    >
      {menus.map(menu => (
        <div key={menu.label} style={{ position: 'relative' }}>
          <div
            onClick={() => setOpenMenu(openMenu === menu.label ? null : menu.label)}
            style={{
              padding: '0.2rem 0.7rem', cursor: 'pointer', fontSize: '0.78rem',
              color: '#cdd6f4', backgroundColor: openMenu === menu.label ? '#313244' : 'transparent',
              borderRadius: '0.25rem', userSelect: 'none',
            }}
          >{menu.label}</div>
          {openMenu === menu.label && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, minWidth: 210,
              backgroundColor: '#1e1e2e', border: '1px solid #313244', borderRadius: '0.3rem',
              padding: '0.25rem 0', zIndex: 1000, boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
            }}>
              {menu.children.map((it, i) => <MenuItemComp key={i} item={it} close={close} />)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function MenuItemComp({ item, close }: { item: MenuItem | MenuSeparator; close: () => void }) {
  const [openSub, setOpenSub] = useState(false);
  if ('type' in item && item.type === 'separator') {
    return <div style={{ height: 1, backgroundColor: '#313244', margin: '0.25rem 0' }} />;
  }
  const it = item as MenuItem;

  if (it.children) {
    return (
      <div style={{ position: 'relative' }} onMouseEnter={() => setOpenSub(true)} onMouseLeave={() => setOpenSub(false)}>
        <div style={rowStyle(false)}
          onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#313244')}
          onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          <span>{it.label}</span>
          <span style={{ color: '#6c7086', fontSize: '0.65rem' }}>▶</span>
        </div>
        {openSub && (
          <div style={{
            position: 'absolute', left: '100%', top: 0, minWidth: 200,
            backgroundColor: '#1e1e2e', border: '1px solid #313244', borderRadius: '0.3rem',
            padding: '0.25rem 0', zIndex: 1001, boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
          }}>
            {it.children.map((sub, j) => <MenuItemComp key={j} item={sub} close={close} />)}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      onClick={() => { if (!it.disabled && it.action) it.action(); }}
      style={{ ...rowStyle(it.disabled), cursor: it.disabled ? 'default' : 'pointer', color: it.disabled ? '#585b70' : '#cdd6f4' }}
      onMouseEnter={e => { if (!it.disabled) e.currentTarget.style.backgroundColor = '#313244'; }}
      onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
        {it.checked !== undefined && (
          <span style={{ width: '1rem', fontSize: '0.65rem', color: '#89b4fa' }}>{it.checked ? '✓' : ''}</span>
        )}
        <span>{it.label}</span>
      </span>
    </div>
  );
}

function rowStyle(disabled?: boolean): React.CSSProperties {
  return {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '0.3rem 0.8rem', fontSize: '0.75rem', userSelect: 'none',
    color: disabled ? '#585b70' : '#cdd6f4',
  };
}
