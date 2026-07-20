import { useState } from 'react';
import type { useSandbox, CatalogueType, OpenDoc } from '../useSandbox.ts';
import { parse as yamlParse } from 'yaml';

export const CATALOGUE_MIME = 'application/mw-catalogue';

interface DragPayload { type: CatalogueType; name: string; }

function readDoc(doc: OpenDoc): any {
  try { return yamlParse(doc.text) || {}; } catch { return null; }
}

export function AgentLegoPanel({ s }: { s: ReturnType<typeof useSandbox> }) {
  const doc = s.activeDoc;

  if (!doc) {
    return <LegoShell><Hint>Ouvrez un agent pour composer ses briques.</Hint></LegoShell>;
  }
  if (doc.type !== 'agents') {
    return (
      <LegoShell>
        <Hint>
          La composition lego cible les <b>agents</b>.<br />
          (Workflow FSM = phase 2, constructeur de skill = phase 3.)
        </Hint>
      </LegoShell>
    );
  }

  const obj = readDoc(doc);
  if (obj === null) {
    return <LegoShell><Hint style={{ color: '#f38ba8' }}>YAML invalide — corrige l'éditeur pour composer.</Hint></LegoShell>;
  }

  const role: string = obj.role || '';
  const tone: string = obj.personality?.tone || '';
  const systemPrompt: string = obj.personality?.system_prompt || '';
  const skills: string[] = Array.isArray(obj.skills) ? obj.skills : [];
  const steps: any[] = obj.workflow?.steps || [];

  return (
    <LegoShell>
      {/* Rôle */}
      <Slot
        title="🏷️ Rôle"
        accepts="roles"
        onDrop={(p) => s.patchActiveDoc(o => { o.role = p.name; })}
      >
        {role ? (
          <Chip label={role} onRemove={() => s.patchActiveDoc(o => { o.role = ''; })} />
        ) : <Empty>Glisse un rôle ici</Empty>}
      </Slot>

      {/* Personnalité */}
      <Slot
        title="🎭 Personnalité"
        accepts="personalities"
        onDrop={async (p) => {
          const pers = await s.fetchItemParsed('personalities', p.name);
          if (pers) s.patchActiveDoc(o => {
            o.personality = { tone: pers.tone || '', system_prompt: pers.system_prompt || '' };
          });
        }}
      >
        {tone || systemPrompt ? (
          <div style={{ fontSize: 12 }}>
            <div style={{ marginBottom: 4 }}>
              <span style={{ opacity: 0.6 }}>tone :</span> {tone || <em style={{ opacity: 0.4 }}>—</em>}
            </div>
            <div style={{
              maxHeight: 90, overflow: 'auto', background: '#181825', borderRadius: 6,
              padding: 6, whiteSpace: 'pre-wrap', color: '#a6adc8', fontSize: 11,
            }}>{systemPrompt || <em style={{ opacity: 0.4 }}>(pas de system_prompt)</em>}</div>
          </div>
        ) : <Empty>Glisse une personnalité ici</Empty>}
      </Slot>

      {/* Skills */}
      <Slot
        title={`🧩 Skills (${skills.length})`}
        accepts="skills"
        onDrop={(p) => s.patchActiveDoc(o => {
          const cur: string[] = Array.isArray(o.skills) ? o.skills : [];
          if (!cur.includes(p.name)) o.skills = [...cur, p.name];
        })}
      >
        {skills.length ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {skills.map((sk, i) => (
              <Chip key={sk + i} label={sk} onRemove={() => s.patchActiveDoc(o => {
                o.skills = (o.skills || []).filter((x: string) => x !== sk);
              })} />
            ))}
          </div>
        ) : <Empty>Glisse des skills ici</Empty>}
      </Slot>

      {/* Workflow */}
      <Slot
        title={`🔀 Workflow (${steps.length} step${steps.length > 1 ? 's' : ''})`}
        accepts="behaviors"
        onDrop={async (p) => {
          const beh = await s.fetchItemParsed('behaviors', p.name);
          if (beh) s.patchActiveDoc(o => {
            o.workflow = beh.workflow || (beh.steps ? { steps: beh.steps } : o.workflow);
          });
        }}
      >
        {steps.length ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {steps.map((st, i) => (
              <div key={st.id || i} style={{
                display: 'flex', alignItems: 'center', gap: 6, fontSize: 11,
                background: '#181825', borderRadius: 6, padding: '3px 6px',
              }}>
                <span style={{ color: STEP_COLOR[st.type] || '#89b4fa', fontWeight: 700 }}>●</span>
                <span style={{ color: '#cdd6f4' }}>{st.id}</span>
                <span style={{ opacity: 0.5 }}>{st.type}</span>
                {st.next && <span style={{ opacity: 0.4, marginLeft: 'auto' }}>→ {st.next}</span>}
              </div>
            ))}
          </div>
        ) : <Empty>Glisse un comportement ici (éditeur graphe = phase 2)</Empty>}
      </Slot>
    </LegoShell>
  );
}

const STEP_COLOR: Record<string, string> = {
  set_variable: '#f9e2af',
  call: '#a6e3a1',
  llm_call: '#89b4fa',
  condition: '#cba6f7',
  end: '#f38ba8',
};

function LegoShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, borderLeft: '1px solid #45475a', background: '#1e1e2e' }}>
      <div style={{ padding: '8px 12px', borderBottom: '1px solid #45475a', fontSize: 13, fontWeight: 600 }}>🧱 Lego — Composition</div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {children}
      </div>
    </div>
  );
}

function Slot({ title, accepts, onDrop, children }: {
  title: string; accepts: CatalogueType; onDrop: (p: DragPayload) => void; children: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  return (
    <div>
      <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>{title}</div>
      <div
        onDragOver={(e) => {
          if (!e.dataTransfer.types.includes(CATALOGUE_MIME)) return;
          e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          setOver(false);
          const raw = e.dataTransfer.getData(CATALOGUE_MIME);
          if (!raw) return;
          e.preventDefault();
          try {
            const p: DragPayload = JSON.parse(raw);
            if (p.type !== accepts) return;
            onDrop(p);
          } catch { /* ignore */ }
        }}
        style={{
          border: `1px dashed ${over ? '#89b4fa' : '#45475a'}`,
          background: over ? 'rgba(137,180,250,0.08)' : '#11111b',
          borderRadius: 8, padding: 8, minHeight: 40,
        }}
      >
        {children}
      </div>
    </div>
  );
}

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, background: '#313244',
      borderRadius: 6, padding: '2px 6px', fontSize: 12,
    }}>
      {label}
      <span onClick={onRemove} style={{ cursor: 'pointer', color: '#f38ba8', fontSize: 12 }}>✕</span>
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <span style={{ opacity: 0.4, fontSize: 12 }}>{children}</span>;
}

function Hint({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={{ opacity: 0.6, fontSize: 12, padding: 8, lineHeight: 1.5, ...style }}>{children}</div>;
}
