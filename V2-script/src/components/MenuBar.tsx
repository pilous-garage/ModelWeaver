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

  return (
    <div className="mw-menu-bar" style={{ display: 'flex', alignItems: 'center', height: 32, padding: '0 6px', borderBottom: '1px solid var(--mw-border, #334155)', flexShrink: 0, background: 'var(--mw-bg, #0f172a)' }}>
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
    return <div className="mw-menu-separator" style={{ width: 1, height: 16, background: 'var(--mw-border, #334155)', margin: '0 6px' }} />;
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

  return (
    <div style={{ position: 'relative' }}>
      <div
        className={`mw-menu-item ${isOpen ? 'mw-menu-item-active' : ''}`}
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
