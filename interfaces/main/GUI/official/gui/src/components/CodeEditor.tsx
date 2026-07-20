import { useRef, useState, useCallback, useEffect } from 'react';

export interface LibResolveResult {
  found: boolean;
  ref?: string;
  qualified?: string;
  module?: string;
  func?: string;
  source?: string;
  file?: string;
}

interface Props {
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  onResolve?: (ref: string) => Promise<LibResolveResult | null>;
}

const EDITOR_STYLE: React.CSSProperties = {
  fontFamily: 'monospace',
  fontSize: 13,
  lineHeight: '20px',
  padding: '12px',
  margin: 0,
  border: 'none',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  overflowWrap: 'break-word',
  tabSize: 2,
};

const LIBREF_RE = /\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,}\b/g;

// Mode debug : affiche un panneau à l'écran (utile sans devtools Tauri).
const DEBUG = true;

function tokenAt(text: string, offset: number): { text: string; start: number; end: number } | null {
  LIBREF_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = LIBREF_RE.exec(text)) !== null) {
    if (offset >= m.index && offset <= m.index + m[0].length) {
      return { text: m[0], start: m.index, end: m.index + m[0].length };
    }
  }
  return null;
}

function offsetWithin(root: Node, node: Node, offset: number): number {
  let total = 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n: Node | null;
  while ((n = walker.nextNode()) !== null) {
    if (n === node) return total + offset;
    total += (n.textContent || '').length;
  }
  return total + offset;
}

function caretOffset(x: number, y: number, mirror: HTMLElement): number | null {
  const doc = document as any;
  if (doc.caretPositionFromPoint) {
    const p = doc.caretPositionFromPoint(x, y);
    if (DEBUG) console.log('[caret] caretPositionFromPoint', !!doc.caretPositionFromPoint, '->', p && p.offsetNode && p.offsetNode.nodeType);
    if (p && p.offsetNode && p.offsetNode.nodeType === 3) {
      return offsetWithin(mirror, p.offsetNode, p.offset);
    }
  } else if (doc.caretRangeFromPoint) {
    const r = doc.caretRangeFromPoint(x, y);
    if (DEBUG) console.log('[caret] caretRangeFromPoint', '->', r && r.startContainer && r.startContainer.nodeType, r && (r.startContainer as any)?.textContent?.slice(0, 20));
    if (r && r.startContainer && r.startContainer.nodeType === 3) {
      return offsetWithin(mirror, r.startContainer, r.startOffset);
    }
  }
  if (DEBUG) console.log('[caret] AUCUN offset pour', x, y);
  return null;
}

function Highlighted({ text }: { text: string }) {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  LIBREF_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = LIBREF_RE.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    nodes.push(
      <span key={i++} style={{ color: '#fab387', background: 'rgba(250,179,135,0.12)', borderRadius: 3 }}>
        {m[0]}
      </span>
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}

export function CodeEditor({ value, onChange, readOnly, onResolve }: Props) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLPreElement>(null);
  const mirRef = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; ref: string } | null>(null);
  const [resolved, setResolved] = useState<LibResolveResult | null>(null);
  const [currentRef, setCurrentRef] = useState<string | null>(null);
  const [debug, setDebug] = useState<string>('');
  const lines = value.split('\n').length;

  const syncScroll = useCallback(() => {
    const ta = taRef.current;
    const pre = preRef.current;
    const mir = mirRef.current;
    if (!ta) return;
    if (pre) { pre.scrollTop = ta.scrollTop; pre.scrollLeft = ta.scrollLeft; }
    if (mir) { mir.scrollTop = ta.scrollTop; mir.scrollLeft = ta.scrollLeft; }
  }, []);

  const hideTip = useCallback(() => { setTip(null); setCurrentRef(null); }, []);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    const ta = taRef.current;
    const mir = mirRef.current;
    if (!ta || !mir || readOnly) return;
    ta.style.pointerEvents = 'none';
    mir.style.pointerEvents = 'auto';
    const off = caretOffset(e.clientX, e.clientY, mir);
    ta.style.pointerEvents = 'auto';
    mir.style.pointerEvents = 'none';
    if (DEBUG) {
      const tok0 = off == null ? null : tokenAt(value, off);
      setDebug(`mouse(${e.clientX|0},${e.clientY|0}) off=${off} tok=${tok0 ? tok0.text : '∅'} ta.pe=${ta.style.pointerEvents}`);
    }
    if (off == null) { hideTip(); return; }
    const tok = tokenAt(value, off);
    if (!tok) { hideTip(); return; }
    setTip({ x: e.clientX, y: e.clientY, ref: tok.text });
    if (tok.text !== currentRef) {
      setCurrentRef(tok.text);
      setResolved(null);
      if (onResolve) {
        onResolve(tok.text).then(r => {
          setCurrentRef(prev => (prev === tok.text ? tok.text : prev));
          setResolved(r);
        }).catch(() => setResolved({ found: false }));
      }
    }
  }, [value, currentRef, readOnly, onResolve, hideTip]);

  useEffect(() => { syncScroll(); }, [value, syncScroll]);

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0, position: 'relative' }}>
      {/* Gouttière numéros de ligne */}
      <div
        style={{
          width: 44, flexShrink: 0, overflow: 'hidden', padding: '12px 4px 12px 0',
          textAlign: 'right', color: '#6c7086', fontFamily: 'monospace', fontSize: 13,
          lineHeight: '20px', background: '#181825', userSelect: 'none',
        }}
      >
        {Array.from({ length: lines }, (_, i) => (
          <div key={i}>{i + 1}</div>
        ))}
      </div>

      {/* Zone éditeur */}
      <div style={{ position: 'relative', flex: 1, minWidth: 0, overflow: 'hidden' }}>
        <pre
          ref={preRef}
          aria-hidden
          style={{
            ...EDITOR_STYLE, position: 'absolute', inset: 0, overflow: 'hidden',
            color: '#cdd6f4', background: 'transparent', pointerEvents: 'none', margin: 0,
          }}
        >
          <Highlighted text={value} />
          {'\n'}
        </pre>

        <div
          ref={mirRef}
          style={{
            ...EDITOR_STYLE, position: 'absolute', inset: 0, overflow: 'hidden',
            opacity: 0, pointerEvents: 'none', color: 'transparent',
          }}
        >{value}</div>

        <textarea
          ref={taRef}
          value={value}
          readOnly={readOnly}
          onChange={(e) => onChange && onChange(e.target.value)}
          onScroll={syncScroll}
          onMouseMove={onMouseMove}
          onMouseLeave={hideTip}
          spellCheck={false}
          style={{
            ...EDITOR_STYLE, position: 'absolute', inset: 0, overflow: 'auto', resize: 'none',
            outline: 'none', color: 'transparent', caretColor: '#cdd6f4', background: 'transparent',
          }}
        />
      </div>

      {tip && (
        <div
          style={{
            position: 'fixed', left: Math.min(tip.x + 12, window.innerWidth - 360),
            top: Math.min(tip.y + 16, window.innerHeight - 240), zIndex: 9999,
            width: 340, maxHeight: 220, overflow: 'auto',
            background: '#11111b', border: '1px solid #45475a', borderRadius: 8,
            padding: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
            fontFamily: 'system-ui, sans-serif', fontSize: 12, color: '#cdd6f4',
          }}
          onMouseEnter={(e) => e.stopPropagation()}
        >
          <div style={{ fontWeight: 700, color: '#fab387', marginBottom: 4 }}>{tip.ref}</div>
          {resolved === null && <div style={{ opacity: 0.6 }}>Résolution…</div>}
          {resolved && !resolved.found && (
            <div style={{ color: '#f38ba8' }}>⚠️ Non résolu dans la librairie</div>
          )}
          {resolved && resolved.found && (
            <>
              <div style={{ opacity: 0.7, marginBottom: 6 }}>
                {resolved.qualified}
                {resolved.file ? `  ·  lib/${resolved.file}` : ''}
              </div>
              <pre style={{
                margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 11,
                color: '#a6e3a1', background: '#181825', padding: 8, borderRadius: 6,
                maxHeight: 150, overflow: 'auto',
              }}>{resolved.source}</pre>
            </>
          )}
        </div>
      )}

      {DEBUG && debug && (
        <div style={{
          position: 'fixed', left: 8, bottom: 8, zIndex: 10000,
          background: 'rgba(17,17,27,0.92)', border: '1px solid #fab387', borderRadius: 6,
          padding: '4px 8px', fontFamily: 'monospace', fontSize: 11, color: '#fab387',
          pointerEvents: 'none', maxWidth: 360,
        }}>
          🐞 {debug}
        </div>
      )}
    </div>
  );
}
