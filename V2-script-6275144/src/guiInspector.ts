// guiInspector — traducteur de fenêtre (DOM texte + coordonnées) et
// simulateur d'actions, sans screenshot.
//
// Un poller (démarré par App) interroge le daemon (gui/poll). S'il y a une
// commande en attente (inspect/act), il l'exécute dans SA propre webview et
// poste le résultat (gui/result). Le backend (mgx/agents/tests) peut donc
// comprendre l'état de la GUI et simuler des actions via les routes gui/*.

import { daemonPost } from './bridge.ts';

export interface DomNode {
  tag: string;
  id?: string;
  testid?: string;
  text?: string;
  box: { x: number; y: number; w: number; h: number };
  draggable?: boolean;
  role?: string;
  children: DomNode[];
}

export interface InspectResult {
  url: string;
  title: string;
  tree: DomNode;
}

/** Extrait l'arbre DOM textuel + coordonnées de la fenêtre courante. */
export function inspectDom(): InspectResult {
  function visible(el: Element): boolean {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function isInteractive(el: Element): boolean {
    const t = el.tagName.toLowerCase();
    const inter = ['button', 'input', 'select', 'textarea', 'a', 'summary', 'label', 'iframe'];
    if (inter.includes(t)) return true;
    const anyEl = el as any;
    if (anyEl.draggable || anyEl.onclick || el.getAttribute('role') || el.getAttribute('data-testid')) return true;
    return false;
  }
  function cleanText(s: string | null | undefined): string {
    if (!s) return '';
    return s.replace(/\s+/g, ' ').trim().slice(0, 120);
  }
  function walk(el: Element, depth: number): DomNode | null {
    if (depth > 12) return null;
    const rect = el.getBoundingClientRect();
    const anyEl = el as any;
    const node: DomNode = {
      tag: el.tagName.toLowerCase(),
      id: el.id || undefined,
      testid: el.getAttribute('data-testid') || undefined,
      text: cleanText((el as any).innerText || (el as any).value || ''),
      box: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
      draggable: !!anyEl.draggable,
      role: el.getAttribute('role') || undefined,
      children: [],
    };
    let keep = isInteractive(el) || !!node.text;
    for (const k of Array.from(el.children)) {
      if (!visible(k)) continue;
      const sub = walk(k, depth + 1);
      if (sub) node.children.push(sub);
    }
    if (!keep && node.children.length === 0) return null;
    return node;
  }
  return {
    url: window.location.href,
    title: document.title,
    tree: walk(document.body, 0) as DomNode,
  };
}

/** Trouve l'élément au point (x,y) le plus profond (pour les actions). */
function elementAtPoint(x: number, y: number): Element | null {
  return document.elementFromPoint(x, y);
}

/** Trouve un élément par data-testid ou id (fallback). */
function findTarget(params: any): Element | null {
  if (params.testid) return document.querySelector(`[data-testid="${params.testid}"]`);
  if (params.id) return document.getElementById(params.id);
  if (params.text) {
    const all = document.querySelectorAll('button, [role="button"], .tab, [draggable="true"], a');
    for (const el of Array.from(all)) {
      if (((el as any).innerText || '').trim() === params.text) return el;
    }
  }
  if (params.x !== undefined && params.y !== undefined) return elementAtPoint(params.x, params.y);
  return null;
}

function dispatchMouse(el: Element, type: string, x: number, y: number): boolean {
  const opts: any = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, detail: 1, view: window };
  const ev = new MouseEvent(type, opts);
  return el.dispatchEvent(ev);
}

/** Simule un clic à (x,y) ou sur un élément ciblé. */
function doClick(params: any): { ok: boolean; info: string; clicked?: boolean } {
  const el = findTarget(params);
  if (!el) return { ok: false, info: 'aucune cible (coordonnées ou testid/id/text requis)' };
  const r = el.getBoundingClientRect();
  const cx = params.x !== undefined ? params.x : Math.round(r.x + r.width / 2);
  const cy = params.y !== undefined ? params.y : Math.round(r.y + r.height / 2);
  dispatchMouse(el, 'mousedown', cx, cy);
  dispatchMouse(el, 'mouseup', cx, cy);
  const clicked = dispatchMouse(el, 'click', cx, cy);
  return { ok: true, info: `click @(${cx},${cy}) sur <${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}>`, clicked: !!clicked };
}

/** mousedown seul (laisse un drag "en cours" pour inspection des marqueurs). */
function doMouseDown(params: any): { ok: boolean; info: string } {
  const el = findTarget(params);
  if (!el) return { ok: false, info: 'cible introuvable' };
  const r = el.getBoundingClientRect();
  const cx = params.x !== undefined ? params.x : Math.round(r.x + r.width / 2);
  const cy = params.y !== undefined ? params.y : Math.round(r.y + r.height / 2);
  dispatchMouse(el, 'mousedown', cx, cy);
  // mousemove léger pour franchir le seuil de drag sans relâcher
  window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, clientX: cx + 8, clientY: cy }));
  window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, clientX: cx + 16, clientY: cy }));
  return { ok: true, info: `mousedown+move @(${cx},${cy}) (drag en cours)` };
}

/** Simule un drag custom souris (mousedown → mousemove → mouseup).
 *  La V2 utilise ce mécanisme (pas de DragEvent natif) pour les onglets :
 *  le mousedown démarre le drag, le mousemove calcule la zone de drop,
 *  le mouseup valide (reorder / move / split selon le bord). */
function doDragMouse(params: any): { ok: boolean; info: string; on?: string } {
  const from = params.from || {};
  const to = params.to || {};
  if (from.x === undefined || from.y === undefined || to.x === undefined || to.y === undefined) {
    return { ok: false, info: 'from{x,y} et to{x,y} requis' };
  }
  const src = elementAtPoint(from.x, from.y);
  if (!src) return { ok: false, info: 'source introuvable' };
  dispatchMouse(src, 'mousedown', from.x, from.y);
  // Un mousemove sur window + vers la cible (calcule la zone de drop)
  const stepCount = 6;
  for (let i = 1; i <= stepCount; i++) {
    const mx = Math.round(from.x + ((to.x - from.x) * i) / stepCount);
    const my = Math.round(from.y + ((to.y - from.y) * i) / stepCount);
    window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, clientX: mx, clientY: my }));
  }
  window.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: to.x, clientY: to.y }));
  const dst = elementAtPoint(to.x, to.y);
  return { ok: true, info: `drag-mouse (${from.x},${from.y}) → (${to.x},${to.y}) sur <${src.tagName.toLowerCase()}>`, on: dst ? `<${dst.tagName.toLowerCase()}>` : 'outside' };
}

/** Simule un drag-and-drop de (from.x,from.y) vers (to.x,to.y). */
function doDrag(params: any): { ok: boolean; info: string } {
  const from = params.from || {};
  const to = params.to || {};
  if (from.x === undefined || from.y === undefined || to.x === undefined || to.y === undefined) {
    return { ok: false, info: 'from{x,y} et to{x,y} requis' };
  }
  const src = elementAtPoint(from.x, from.y);
  const dst = elementAtPoint(to.x, to.y);
  if (!src) return { ok: false, info: 'source introuvable' };
  // Drag HTML5 : notre drag d'onglets utilise dataTransfer avec un type custom.
  const dt = new DataTransfer();
  const dragStart = new DragEvent('dragstart', { bubbles: true, cancelable: true, clientX: from.x, clientY: from.y, dataTransfer: dt });
  src.dispatchEvent(dragStart);
  if (dst) {
    const over = new DragEvent('dragover', { bubbles: true, cancelable: true, clientX: to.x, clientY: to.y, dataTransfer: dt });
    dst.dispatchEvent(over);
    const drop = new DragEvent('drop', { bubbles: true, cancelable: true, clientX: to.x, clientY: to.y, dataTransfer: dt });
    dst.dispatchEvent(drop);
  }
  const dragEnd = new DragEvent('dragend', { bubbles: true, cancelable: true, clientX: to.x, clientY: to.y, dataTransfer: dt });
  src.dispatchEvent(dragEnd);
  return { ok: true, info: `drag (${from.x},${from.y}) → (${to.x},${to.y}) sur <${src.tagName.toLowerCase()}>` };
}

/** Simule la saisie de texte dans un input/textarea ciblé. */
function doType(params: any): { ok: boolean; info: string } {
  const el = findTarget(params);
  if (!el) return { ok: false, info: 'cible introuvable' };
  const input = el as HTMLInputElement;
  const anyInput = el as any;
  if (typeof input.value === 'string' && typeof anyInput.setNativeValue !== 'function') {
    // setter natif pour déclencher React onChange
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(input, params.text || '');
    else input.value = params.text || '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, info: `type '${params.text}' dans <${el.tagName.toLowerCase()}>` };
  }
  input.value = params.text || '';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return { ok: true, info: `type '${params.text}' dans <${el.tagName.toLowerCase()}>` };
}

/** Exécute une commande d'action (act). */
export function runAct(action: string, params: any): { ok: boolean; info: string } {
  switch (action) {
    case 'click': return doClick(params);
    case 'mousedown': return doMouseDown(params);
    case 'drag': return doDrag(params);
    case 'drag-mouse': return doDragMouse(params);
    case 'type': return doType(params);
    case 'hover': {
      const el = findTarget(params);
      if (!el) return { ok: false, info: 'cible introuvable' };
      const r = el.getBoundingClientRect();
      const cx = params.x !== undefined ? params.x : Math.round(r.x + r.width / 2);
      const cy = params.y !== undefined ? params.y : Math.round(r.y + r.height / 2);
      el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, clientX: cx, clientY: cy }));
      el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: false, cancelable: false, clientX: cx, clientY: cy }));
      el.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, clientX: cx, clientY: cy }));
      const anyEl = el as any;
      if (typeof anyEl.onmouseenter === 'function') anyEl.onmouseenter(new MouseEvent('mouseenter', { bubbles: false, clientX: cx, clientY: cy }));
      if (typeof anyEl.onmouseover === 'function') anyEl.onmouseover(new MouseEvent('mouseover', { bubbles: true, clientX: cx, clientY: cy }));
      return { ok: true, info: `hover @(${cx},${cy}) sur <${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}>` };
    }
    case 'hover-out': {
      const el = findTarget(params);
      if (!el) return { ok: false, info: 'cible introuvable' };
      const r = el.getBoundingClientRect();
      const cx = params.x !== undefined ? params.x : Math.round(r.x + r.width / 2);
      const cy = params.y !== undefined ? params.y : Math.round(r.y + r.height / 2);
      el.dispatchEvent(new MouseEvent('mouseout', { bubbles: true, cancelable: true, clientX: cx, clientY: cy }));
      el.dispatchEvent(new MouseEvent('mouseleave', { bubbles: false, cancelable: false, clientX: cx, clientY: cy }));
      const anyEl = el as any;
      if (typeof anyEl.onmouseleave === 'function') anyEl.onmouseleave(new MouseEvent('mouseleave', { bubbles: false, clientX: cx, clientY: cy }));
      return { ok: true, info: `hover-out @(${cx},${cy}) sur <${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}>` };
    }
    case 'mousemove': {
      const el = params.x !== undefined && params.y !== undefined ? elementAtPoint(params.x, params.y) : null;
      const cx = params.x !== undefined ? params.x : 0;
      const cy = params.y !== undefined ? params.y : 0;
      const target = el || document.body;
      target.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, clientX: cx, clientY: cy }));
      target.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, clientX: cx, clientY: cy }));
      return { ok: true, info: `mousemove @(${cx},${cy}) sur <${target.tagName.toLowerCase()}>` };
    }
    default:
      return { ok: false, info: `action inconnue: ${action}` };
  }
}

// ── Poller ──────────────────────────────────────────────────────────

declare global { interface Window { __MW_GUI_POLLING__?: boolean } }

function _isPolling(): boolean {
  return !!(typeof window !== 'undefined' && (window as any).__MW_GUI_POLLING__);
}

/** Boucle de poll : interroge le daemon, exécute les commandes, poste les
 *  résultats. Chaque webview ne traite que les commandes visant SA fenêtre
 *  (paramètre window de gui/poll). */
export async function startGuiInspectorPoll(intervalMs = 1500): Promise<void> {
  if (_isPolling()) return;
  (window as any).__MW_GUI_POLLING__ = true;
  console.log('[guiInspector] poller démarré');
  const tick = async () => {
    try {
      const selfLabel = (window as any).__MW_WINDOW_LABEL || null;
      const res = await daemonPost('gui/poll', { window: selfLabel || undefined });
      const commands = res?.result?.commands || res?.commands || [];
      for (const cmd of commands) {
        const r = await executeCommand(cmd);
        await daemonPost('gui/result', { command_id: cmd.id, ok: r.ok, result: r.result });
      }
    } catch (e: any) {
      console.warn('[guiInspector] poll échec:', e?.message);
    }
  };
  await tick();
  const loop = async () => {
    while (_isPolling()) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
      await tick();
    }
  };
  loop();
}

async function executeCommand(cmd: any): Promise<{ ok: boolean; result: any }> {
  try {
    if (cmd.type === 'inspect') {
      const dom = inspectDom();
      return { ok: true, result: { kind: 'dom', ...dom } };
    }
    if (cmd.type === 'act') {
      const p = cmd.params || {};
      const r = runAct(p.action, p);
      return { ok: r.ok, result: { kind: 'act', action: p.action, ...r } };
    }
    return { ok: false, result: { kind: 'unknown', type: cmd.type } };
  } catch (e: any) {
    return { ok: false, result: { kind: 'error', error: String(e?.message || e) } };
  }
}

export function stopGuiInspectorPoll() {
  if (typeof window !== 'undefined') (window as any).__MW_GUI_POLLING__ = false;
}
