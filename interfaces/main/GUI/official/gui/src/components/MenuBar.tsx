import React, { useState, useRef, useEffect } from 'react';
import { WebviewWindow } from '@tauri-apps/api/webviewWindow';
import type { AppApi } from '../useApp.ts';

export function openSandboxWindow() {
  const existing = WebviewWindow.getByLabel('sandbox');
  if (existing) {
    existing.show().catch(() => {});
    existing.setFocus().catch(() => {});
    return;
  }
  const w = new WebviewWindow('sandbox', {
    title: 'ModelWeaver — Agent Sandbox',
    width: 1400,
    height: 900,
    url: '/',
  });
  w.once('tauri://created', () => {});
  w.once('tauri://error', (e) => console.error('sandbox window error', e));
}

interface Menu {
  label: string;
  children: (MenuItem | MenuSeparator)[];
}

interface MenuItem {
  type: 'item';
  label: string;
  action: () => void;
  checked?: boolean;
  disabled?: boolean;
}

interface MenuSeparator {
  type: 'separator';
}

const PANEL_LABELS: Record<string, string> = {
  'system-state': 'État du système',
  'resources': 'Ressources',
  'installed-tools': 'Outils installés',
  'catalogue': 'Catalogue',
  'chat': 'Chat',
  'install-queue': "File d'installation",
  'agents': 'Agents',
  'local-models': 'LLM locaux',
  'keys': 'Clés API',
  'debug': 'Debug',
};

export function MenuBar({ app }: { app: AppApi }) {
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) {
        setOpenMenu(null);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const menus: Menu[] = [
    {
      label: 'Fichier',
      children: [
        { type: 'item', label: 'Enregistrer la disposition…', action: () => { app.saveLayout(); setOpenMenu(null); } },
        { type: 'item', label: 'Charger une disposition…', action: () => { app.loadLayout(); setOpenMenu(null); } },
        { type: 'separator' },
        { type: 'item', label: 'Quitter', action: () => { setOpenMenu(null); } },
      ],
    },
    {
      label: 'Affichage',
      children: [
        {
          type: 'item', label: 'Panneaux',
          action: () => {},
          children: app.ALL_PANELS.map(id => ({
            type: 'item' as const,
            label: PANEL_LABELS[id] || id,
            checked: !app.hiddenPanels[id],
            action: () => { app.togglePanelVisibility(id); setOpenMenu(null); },
          })),
        },
      ],
    },
    {
      label: 'Outils',
      children: [
        { type: 'item', label: '🛠️ Agent Sandbox (IDE)', action: () => { openSandboxWindow(); setOpenMenu(null); } },
      ],
    },
  ];

  return (
    <div
      ref={barRef}
      style={{
        display: 'flex',
        alignItems: 'center',
        padding: '0 0.5rem',
        backgroundColor: '#1e293b',
        borderBottom: '1px solid #334155',
        flexShrink: 0,
        height: '1.8rem',
        gap: 0,
      }}
    >
      {menus.map(menu => (
        <div key={menu.label} style={{ position: 'relative' }}>
          <div
            onClick={() => setOpenMenu(openMenu === menu.label ? null : menu.label)}
            style={{
              padding: '0.2rem 0.7rem',
              cursor: 'pointer',
              fontSize: '0.78rem',
              color: '#e2e8f0',
              backgroundColor: openMenu === menu.label ? '#334155' : 'transparent',
              borderRadius: '0.25rem',
              userSelect: 'none',
            }}
          >
            {menu.label}
          </div>

          {openMenu === menu.label && (
            <div
              style={{
                position: 'absolute',
                top: '100%',
                left: 0,
                minWidth: '200px',
                backgroundColor: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '0.3rem',
                padding: '0.25rem 0',
                zIndex: 1000,
                boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
              }}
            >
              {renderMenuItems(menu.children, () => setOpenMenu(null))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function renderMenuItems(items: (MenuItem | MenuSeparator)[], close: () => void): React.ReactNode {
  return items.map((item, i) => {
    if (item.type === 'separator') {
      return <div key={i} style={{ height: '1px', backgroundColor: '#334155', margin: '0.25rem 0' }} />;
    }
    return <MenuItemComponent key={i} item={item} close={close} />;
  });
}

function MenuItemComponent({ item, close }: { item: MenuItem; close: () => void }) {
  const [openSub, setOpenSub] = useState(false);

  if ('children' in item && item.children) {
    return (
      <div
        style={{ position: 'relative' }}
        onMouseEnter={() => setOpenSub(true)}
        onMouseLeave={() => setOpenSub(false)}
      >
        <div
          onClick={() => { item.action(); close(); }}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0.3rem 0.8rem',
            cursor: 'pointer',
            fontSize: '0.75rem',
            color: '#e2e8f0',
            userSelect: 'none',
          }}
          onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#334155')}
          onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          <span>{item.label}</span>
          <span style={{ color: '#64748b', fontSize: '0.65rem' }}>▶</span>
        </div>
        {openSub && (
          <div
            style={{
              position: 'absolute',
              left: '100%',
              top: 0,
              minWidth: '200px',
              backgroundColor: '#1e293b',
              border: '1px solid #334155',
              borderRadius: '0.3rem',
              padding: '0.25rem 0',
              zIndex: 1001,
              boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            }}
          >
            {item.children.map((sub, j) => {
              if (sub.type === 'separator') {
                return <div key={j} style={{ height: '1px', backgroundColor: '#334155', margin: '0.25rem 0' }} />;
              }
              return (
                <div
                  key={j}
                  onClick={() => { sub.action(); close(); }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.4rem',
                    padding: '0.3rem 0.8rem',
                    cursor: 'pointer',
                    fontSize: '0.75rem',
                    color: '#e2e8f0',
                    userSelect: 'none',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#334155')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <span style={{ width: '1rem', fontSize: '0.65rem', color: '#60a5fa' }}>
                    {sub.checked ? '✓' : ''}
                  </span>
                  <span>{sub.label}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      onClick={() => { item.action(); close(); }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.4rem',
        padding: '0.3rem 0.8rem',
        cursor: item.disabled ? 'default' : 'pointer',
        fontSize: '0.75rem',
        color: item.disabled ? '#64748b' : '#e2e8f0',
        userSelect: 'none',
      }}
      onMouseEnter={e => { if (!item.disabled) e.currentTarget.style.backgroundColor = '#334155'; }}
      onMouseLeave={e => e.currentTarget.style.backgroundColor = 'transparent'}
    >
      {item.checked !== undefined && (
        <span style={{ width: '1rem', fontSize: '0.65rem', color: '#60a5fa' }}>
          {item.checked ? '✓' : ''}
        </span>
      )}
      <span>{item.label}</span>
    </div>
  );
}
