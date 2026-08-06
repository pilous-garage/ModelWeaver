// MenuBar — barre de menu avec gestion correcte du survol.
//
// Deux états DISTINCTS :
//   - openPath : chemin du menu ouvert (sous-menus visibles, fond accent).
//     S'ouvre au clic sur la racine ; au survol d'un autre item racine on
//     navigue ; au survol d'un sous-menu on l'ouvre et on ferme les voisins.
//   - hoverPath : surbrillance TEMPORAIRE de l'item survolé (fond clair).
//     Disparaît au mouseleave (fix : l'item n'est plus surligné après l'avoir
//     quitté, et les sous-menus traversés ne restent plus tous ouverts).

import React, { useEffect, useState } from 'react';
import type { MenuItem } from '../layout/types.ts';

interface Props {
  items: MenuItem[];
  t: (k: string) => string;
  onAction(action: string): void;
}

export function MenuBar({ items, t, onAction }: Props) {
  const [openPath, setOpenPath] = useState<number[] | null>(null);
  const [hoverPath, setHoverPath] = useState<number[] | null>(null);

  // Ferme le menu si on clique hors des items de menu (y compris sur la barre
  // vide entre les menus) ou hors de la barre.
  useEffect(() => {
    if (!openPath) return;
    const handle = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!(t instanceof Element)) return;
      const onItem = !!t.closest('.mw-menu-item') || !!t.closest('[class*="mw-menu-item"]');
      if (!onItem) {
        setOpenPath(null);
        setHoverPath(null);
      }
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [openPath]);

  // Navigation clavier : Échap ferme le menu, flèches Gauche/Droite naviguent
  // entre les menus racines quand un menu est ouvert (comportement desktop).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpenPath(null);
        setHoverPath(null);
        return;
      }
      if (!openPath) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const dir = e.key === 'ArrowRight' ? 1 : -1;
        const rootIdx = openPath[0];
        const next = Math.min(Math.max(rootIdx + dir, 0), items.length - 1);
        if (next !== rootIdx) setOpenPath([next]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openPath, items.length]);

  return (
    <div
      className="mw-menu-bar"
      role="menubar"
      aria-label="menu principal"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          setOpenPath(null);
          setHoverPath(null);
        }
      }}
      style={{ display: 'flex', alignItems: 'center', height: 32, padding: '0 6px', borderBottom: '1px solid var(--mw-border, #334155)', flexShrink: 0, background: 'var(--mw-bg, #0f172a)' }}>
      {items.map((item, i) => (
        <MenuItem
          key={item.id || i}
          item={item}
          t={t}
          onAction={onAction}
          path={[i]}
          openPath={openPath}
          hoverPath={hoverPath}
          onOpen={(p) => setOpenPath(p)}
          onClose={() => setOpenPath(null)}
          onHover={(p) => setHoverPath(p)}
          onLeave={() => setHoverPath(null)}
        />
      ))}
    </div>
  );
}

interface ItemProps {
  item: MenuItem;
  t: (k: string) => string;
  onAction(a: string): void;
  path: number[];
  openPath: number[] | null;
  hoverPath: number[] | null;
  onOpen(p: number[]): void;
  onClose(): void;
  onHover(p: number[] | null): void;
  onLeave(): void;
}

function MenuItem({ item, t, onAction, path, openPath, hoverPath, onOpen, onClose, onHover, onLeave }: ItemProps) {
  const depth = path.length - 1;

  // Ce menu est-il ouvert ? (le chemin d'ouverture commence par ce chemin)
  const isOpen = !!openPath && openPath.length >= path.length && path.every((v, i) => openPath[i] === v);
  // L'item fait-il partie du chemin survolé (surbrillance) ?
  const isHovered = !!hoverPath && hoverPath.length >= path.length && path.every((v, i) => hoverPath[i] === v);
  const isLeaf = !item.items || item.items.length === 0;

  if (item.type === 'separator') {
    // Barre racine → séparateur vertical ; sous-menu → ligne horizontale.
    const sep = depth === 0
      ? { width: 1, height: 16, background: 'var(--mw-border, #334155)', margin: '0 6px' }
      : { height: 1, background: 'var(--mw-border, #334155)', margin: '4px 2px' };
    return <div className="mw-menu-separator" style={{ ...sep, flexShrink: 0 }} />;
  }

  const label = item.labelKey ? t(item.labelKey) : '';

  const handleMouseEnter = () => {
    onHover(path);
    if (isLeaf) {
      // item d'action : rien à ouvrir, mais on garde la surbrillance du parent
    } else if (depth === 0) {
      // survol d'un menu racine : on navigue (n'ouvre que si un menu est déjà ouvert,
      // sinon il faut un clic pour ouvrir le premier)
      if (openPath) onOpen(path);
    } else {
      // sous-menu : on l'ouvre (ferme les voisins via le nouveau path)
      onOpen(path);
    }
  };

  const handleClick = () => {
    if (isLeaf) {
      if (item.action) onAction(item.action);
      onClose();
      onLeave();
    } else if (depth === 0) {
      if (isOpen) onClose();
      else onOpen(path);
    }
  };

  // Fond : item du chemin OUVERT → accent ; sinon survolé → hover ; sinon transparent.
  const background = isOpen
    ? 'var(--mw-accent, #0f3460)'
    : isHovered
      ? 'var(--mw-bg-hover, #243349)'
      : 'transparent';

  // Entête de section de panel : non cliquable, label en gras/dim.
  if (item.style === 'section-header') {
    return (
      <div
        data-testid={item.labelKey ? `menu-hdr-${item.labelKey.replace(/[^a-z0-9-]/gi, '-')}` : undefined}
        style={{
          padding: '4px 10px', fontSize: 11, fontWeight: 700, letterSpacing: .3,
          color: '#94a3b8', background: 'var(--mw-bg, #0f172a)',
          borderBottom: '1px solid var(--mw-border, #334155)', margin: '2px 0',
          textTransform: 'uppercase', userSelect: 'none', pointerEvents: 'none',
        }}
      >{label}</div>
    );
  }

  return (
    <div style={{ position: 'relative' }}>
      <div
        className={`mw-menu-item ${isOpen ? 'mw-menu-item-active' : ''}`}
        role="menuitem"
        aria-haspopup={!isLeaf ? 'true' : undefined}
        aria-expanded={!isLeaf ? isOpen : undefined}
        data-testid={
          item.action
            ? `menu-${item.action.replace(/[^a-z0-9-]/gi, '-')}`
            : item.labelKey
              ? `menu-${item.labelKey.replace(/[^a-z0-9-]/gi, '-')}`
              : undefined
        }
        onClick={handleClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={onLeave}
        style={{
          padding: '5px 10px', cursor: 'pointer', fontSize: 12, whiteSpace: 'nowrap',
          display: 'flex', alignItems: 'center', gap: 6,
          background,
          color: 'var(--mw-fg, #e2e8f0)', borderRadius: 4,
          ...(item.disabled ? { opacity: 0.5, pointerEvents: 'none' as const } : {}),
        }}
      >
        <span>{label}</span>
        {item.checked !== undefined && <span>{item.checked ? '☑' : '☐'}</span>}
        {item.suffix !== undefined && <span style={{ color: '#94a3b8', marginLeft: 4 }}>{item.suffix}</span>}
        {!isLeaf && <span style={{ fontSize: 9 }}>▶</span>}
      </div>
      {!isLeaf && isOpen && (
        <div style={{
          position: 'absolute', top: '100%', left: depth > 0 ? '100%' : 0, zIndex: 1000,
          background: 'var(--mw-bg-panel, #1e293b)', border: '1px solid var(--mw-border, #334155)',
          borderRadius: 6, minWidth: 200, padding: 4, boxShadow: '0 4px 12px rgba(0,0,0,.3)',
        }}>
          {item.items!.map((sub, i) => (
            <MenuItem
              key={sub.id || i}
              item={sub}
              t={t}
              onAction={onAction}
              path={[...path, i]}
              openPath={openPath}
              hoverPath={hoverPath}
              onOpen={onOpen}
              onClose={onClose}
              onHover={onHover}
              onLeave={onLeave}
            />
          ))}
        </div>
      )}
    </div>
  );
}
