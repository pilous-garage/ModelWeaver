import type { Step } from '../lib/workflowGraph.ts';
import { stepMeta } from '../lib/workflowGraph.ts';
import YAML from 'yaml';

const OPERATORS = ['EQUALS', 'NOT_EQUALS', 'CONTAINS', 'GREATER', 'LESS'];
const COND_OPERATORS = ['TRUTHY', 'EQUALS', 'NOT_EQUALS', 'CONTAINS', 'GREATER', 'LESS'];

interface Props {
  step: Step | null;
  nodeIds: string[];
  parentId?: string;
  loopIds?: string[];
  onChange: (patch: Partial<Step>) => void;
  onRenameId: (newId: string) => void;
  onSetParent?: (parentId: string | undefined) => void;
  onDelete: () => void;
}

export function NodeInspector({ step, nodeIds, parentId, loopIds = [], onChange, onRenameId, onSetParent, onDelete }: Props) {
  if (!step) {
    return (
      <Shell>
        <div style={{ opacity: 0.4, fontSize: 12, padding: 10 }}>
          Sélectionne un nœud pour éditer ses propriétés.
        </div>
      </Shell>
    );
  }

  const meta = stepMeta(step.type);
  const targets = nodeIds.filter(id => id !== step.id);

  return (
    <Shell>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ color: meta.color, fontSize: 16 }}>{meta.icon}</span>
        <strong style={{ fontSize: 13 }}>{meta.label}</strong>
        <div style={{ flex: 1 }} />
        <button onClick={onDelete} style={delBtn}>🗑️ Supprimer</button>
      </div>

      <Field label="id">
        <input
          value={step.id}
          onChange={e => onRenameId(e.target.value)}
          style={input}
        />
      </Field>

      {onSetParent && (
        <Field label="conteneur (boucle)">
          <select value={parentId || ''} onChange={e => onSetParent(e.target.value || undefined)} style={input}>
            <option value="">(racine)</option>
            {loopIds.map(l => <option key={l} value={l}>{l}</option>)}
            {parentId && !loopIds.includes(parentId) && <option value={parentId}>{parentId}</option>}
          </select>
        </Field>
      )}

      {step.type === 'for' && (
        <>
          <Field label="var"><input value={step.var || ''} onChange={e => onChange({ var: e.target.value })} style={input} /></Field>
          <Field label="mode">
            <select
              value={'items' in step ? 'list' : 'range'}
              onChange={e => e.target.value === 'list'
                ? onChange({ items: step.items ?? '', start: undefined, end: undefined, step: undefined })
                : onChange({ items: undefined, start: step.start ?? 0, end: step.end ?? 3, step: step.step ?? 1 })}
              style={input}
            >
              <option value="range">plage (start/end/step)</option>
              <option value="list">liste (items)</option>
            </select>
          </Field>
          {'items' in step ? (
            <Field label="items (liste ou {{var}})"><input value={step.items ?? ''} onChange={e => onChange({ items: e.target.value })} style={input} /></Field>
          ) : (
            <Row>
              <Field label="start"><input type="number" value={step.start ?? 0} onChange={e => onChange({ start: Number(e.target.value) })} style={input} /></Field>
              <Field label="end"><input type="number" value={step.end ?? 0} onChange={e => onChange({ end: Number(e.target.value) })} style={input} /></Field>
              <Field label="step"><input type="number" value={step.step ?? 1} onChange={e => onChange({ step: Number(e.target.value) })} style={input} /></Field>
            </Row>
          )}
          <TargetField label="next (après boucle)" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
          <Note>Astuce : glisse des skills dans la boîte pour remplir le corps.</Note>
        </>
      )}

      {step.type === 'while' && (
        <>
          <div style={{ fontSize: 11, opacity: 0.7, margin: '4px 0 2px' }}>condition</div>
          <Field label="variable"><input value={step.condition?.variable || ''} onChange={e => onChange({ condition: { ...(step.condition || {}), variable: e.target.value } })} style={input} /></Field>
          <Row>
            <Field label="operator">
              <select value={step.condition?.operator || 'TRUTHY'} onChange={e => onChange({ condition: { ...(step.condition || {}), operator: e.target.value } })} style={input}>
                {COND_OPERATORS.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </Field>
            <Field label="value"><input value={step.condition?.value ?? ''} onChange={e => onChange({ condition: { ...(step.condition || {}), value: e.target.value } })} style={input} /></Field>
          </Row>
          <TargetField label="next (après boucle)" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
          <Note>Astuce : glisse des skills dans la boîte pour remplir le corps.</Note>
        </>
      )}

      {step.type === 'if' && (
        <>
          <div style={{ fontSize: 11, opacity: 0.7, margin: '4px 0 2px' }}>condition</div>
          <Field label="variable"><input value={step.condition?.variable || ''} onChange={e => onChange({ condition: { ...(step.condition || {}), variable: e.target.value } })} style={input} /></Field>
          <Row>
            <Field label="operator">
              <select value={step.condition?.operator || 'TRUTHY'} onChange={e => onChange({ condition: { ...(step.condition || {}), operator: e.target.value } })} style={input}>
                {COND_OPERATORS.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </Field>
            <Field label="value"><input value={step.condition?.value ?? ''} onChange={e => onChange({ condition: { ...(step.condition || {}), value: e.target.value } })} style={input} /></Field>
          </Row>
          <TargetField label="next (après if)" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
          <Note>Si vrai → exécute le corps ; si faux → va à next.</Note>
        </>
      )}

      {step.type === 'llm_call' && (
        <>
          <Field label="skill_prompt">
            <textarea value={step.skill_prompt || ''} onChange={e => onChange({ skill_prompt: e.target.value })} style={{ ...input, minHeight: 70, resize: 'vertical' }} />
          </Field>
          <Row>
            <Field label="provider_ref"><input value={step.provider_ref || ''} onChange={e => onChange({ provider_ref: e.target.value })} style={input} /></Field>
            <Field label="model_ref"><input value={step.model_ref || ''} onChange={e => onChange({ model_ref: e.target.value })} style={input} /></Field>
          </Row>
          <Row>
            <Field label="temperature"><input type="number" step="0.1" value={step.temperature ?? ''} onChange={e => onChange({ temperature: e.target.value === '' ? undefined : Number(e.target.value) })} style={input} /></Field>
            <Field label="max_tokens"><input type="number" value={step.max_tokens ?? ''} onChange={e => onChange({ max_tokens: e.target.value === '' ? undefined : Number(e.target.value) })} style={input} /></Field>
          </Row>
          <Field label="output_capture"><input value={step.output_capture || ''} onChange={e => onChange({ output_capture: e.target.value })} style={input} /></Field>
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
          <TargetField label="on_error" value={step.on_error} targets={targets} onChange={v => onChange({ on_error: v })} />
        </>
      )}

      {step.type === 'call' && (
        <>
          <Field label="fn (skill)"><input value={step.fn || ''} onChange={e => onChange({ fn: e.target.value })} style={input} /></Field>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, marginBottom: 8, cursor: 'pointer', color: step.uses_llm ? '#a6e3a1' : '#6c7086' }}>
            <input type="checkbox" checked={!!step.uses_llm} onChange={e => onChange({ uses_llm: e.target.checked })} />
            🧠 utilise un LLM
          </label>
          <MapField label="inputs" value={step.inputs || {}} onChange={v => onChange({ inputs: v })} />
          <MapField label="capture (sortie → variable)" value={typeof step.capture === 'object' ? step.capture : {}} onChange={v => onChange({ capture: v })} />
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
          <TargetField label="on_error" value={step.on_error} targets={targets} onChange={v => onChange({ on_error: v })} />
        </>
      )}

      {step.type === 'agent_call' && (
        <>
          <Row>
            <Field label="agent"><input value={step.agent || ''} onChange={e => onChange({ agent: e.target.value })} style={input} /></Field>
            <Field label="entrypoint"><input value={step.entrypoint || 'main'} onChange={e => onChange({ entrypoint: e.target.value })} style={input} /></Field>
          </Row>
          <MapField label="inputs" value={step.inputs || {}} onChange={v => onChange({ inputs: v })} />
          <MapField label="capture (sortie → variable)" value={typeof step.capture === 'object' ? step.capture : {}} onChange={v => onChange({ capture: v })} />
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
          <TargetField label="on_error" value={step.on_error} targets={targets} onChange={v => onChange({ on_error: v })} />
        </>
      )}

      {(step.type === 'break' || step.type === 'continue') && (
        <Note>
          {step.type === 'break' ? 'Sort de la boucle englobante.' : 'Passe à l\u2019itération suivante.'}
          {' '}À placer dans une boîte for/while (voir « conteneur »).
        </Note>
      )}

      {step.type === 'tool_call' && (
        <>
          <Field label="tool"><input value={step.tool || ''} onChange={e => onChange({ tool: e.target.value })} style={input} /></Field>
          <MapField label="args" value={step.args || {}} onChange={v => onChange({ args: v })} />
          <Field label="output_capture"><input value={step.output_capture || ''} onChange={e => onChange({ output_capture: e.target.value })} style={input} /></Field>
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
        </>
      )}

      {step.type === 'switch' && (
        <>
          <Field label="variable"><input value={step.variable || ''} onChange={e => onChange({ variable: e.target.value })} style={input} /></Field>
          <div style={{ fontSize: 11, opacity: 0.7, margin: '6px 0 4px' }}>conditions</div>
          {(step.conditions || []).map((c: any, i: number) => (
            <div key={i} style={{ border: '1px solid #313244', borderRadius: 6, padding: 6, marginBottom: 6 }}>
              <Row>
                <select value={c.operator || 'EQUALS'} onChange={e => updateCond(step, onChange, i, { operator: e.target.value })} style={input}>
                  {OPERATORS.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
                <input placeholder="value" value={c.value ?? ''} onChange={e => updateCond(step, onChange, i, { value: e.target.value })} style={input} />
              </Row>
              <TargetField label="→ next" value={c.next} targets={targets} onChange={v => updateCond(step, onChange, i, { next: v })} />
              <button onClick={() => removeCond(step, onChange, i)} style={{ ...delBtn, marginTop: 4 }}>retirer</button>
            </div>
          ))}
          <button onClick={() => addCond(step, onChange)} style={addBtn}>+ condition</button>
          <TargetField label="default" value={step.default} targets={targets} onChange={v => onChange({ default: v })} />
        </>
      )}

      {step.type === 'set_variable' && (
        <>
          <Field label="name"><input value={step.name || ''} onChange={e => onChange({ name: e.target.value })} style={input} /></Field>
          <Field label="value"><input value={step.value ?? ''} onChange={e => onChange({ value: e.target.value })} style={input} /></Field>
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
        </>
      )}

      {step.type === 'sleep' && (
        <>
          <Field label="duration_seconds"><input type="number" value={step.duration_seconds ?? ''} onChange={e => onChange({ duration_seconds: e.target.value === '' ? undefined : Number(e.target.value) })} style={input} /></Field>
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
        </>
      )}

      {step.type === 'spawn' && (
        <>
          <Row>
            <Field label="name"><input value={step.name || ''} onChange={e => onChange({ name: e.target.value })} style={input} /></Field>
            <Field label="role"><input value={step.role || ''} onChange={e => onChange({ role: e.target.value })} style={input} /></Field>
          </Row>
          <Field label="occupation"><input value={step.occupation || ''} onChange={e => onChange({ occupation: e.target.value })} style={input} /></Field>
          <Field label="request"><textarea value={step.request || ''} onChange={e => onChange({ request: e.target.value })} style={{ ...input, minHeight: 50, resize: 'vertical' }} /></Field>
          <Row>
            <Field label="provider_ref"><input value={step.provider_ref || ''} onChange={e => onChange({ provider_ref: e.target.value })} style={input} /></Field>
            <Field label="model_ref"><input value={step.model_ref || ''} onChange={e => onChange({ model_ref: e.target.value })} style={input} /></Field>
          </Row>
          <Field label="output_capture"><input value={step.output_capture || ''} onChange={e => onChange({ output_capture: e.target.value })} style={input} /></Field>
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
        </>
      )}

      {step.type === 'handoff' && (
        <>
          <Field label="to"><input value={step.to || ''} onChange={e => onChange({ to: e.target.value })} style={input} /></Field>
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
        </>
      )}

      {step.type === 'group' && (
        <>
          <Note>Groupe de steps — tous les steps dans le corps sont exécutés séquentiellement.</Note>
          <TargetField label="next" value={step.next} targets={targets} onChange={v => onChange({ next: v })} />
          <Note>Astuce : glisse des steps dans la boîte pour remplir le groupe.</Note>
        </>
      )}

      {step.type === 'end' && (
        <Field label="status">
          <select value={step.status || 'SUCCESS'} onChange={e => onChange({ status: e.target.value })} style={input}>
            <option value="SUCCESS">SUCCESS</option>
            <option value="FAILED">FAILED</option>
          </select>
        </Field>
      )}

      <div style={{ marginTop: 16, borderTop: '1px solid #45475a', paddingTop: 8 }}>
        <div style={{ fontSize: 10, opacity: 0.6, marginBottom: 4 }}>YAML</div>
        <pre style={{
          background: '#11111b', color: '#a6adc8', padding: 8, borderRadius: 6,
          fontSize: 11, lineHeight: 1.5, overflowX: 'auto', whiteSpace: 'pre', margin: 0,
        }}>{stepToYaml(step)}</pre>
      </div>
    </Shell>
  );
}

function updateCond(step: Step, onChange: (p: Partial<Step>) => void, i: number, patch: any) {
  const conditions = [...(step.conditions || [])];
  conditions[i] = { ...conditions[i], ...patch };
  onChange({ conditions });
}
function addCond(step: Step, onChange: (p: Partial<Step>) => void) {
  const conditions = [...(step.conditions || []), { operator: 'EQUALS', value: '', next: '' }];
  onChange({ conditions });
}
function removeCond(step: Step, onChange: (p: Partial<Step>) => void, i: number) {
  const conditions = (step.conditions || []).filter((_: any, j: number) => j !== i);
  onChange({ conditions });
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 10, borderLeft: '1px solid #45475a', background: '#1e1e2e' }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>🔍 Inspecteur</div>
      {children}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8, flex: 1, minWidth: 0 }}>
      <label style={{ display: 'block', fontSize: 10, opacity: 0.6, marginBottom: 2 }}>{label}</label>
      {children}
    </div>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'flex', gap: 6 }}>{children}</div>;
}

function Note({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 10, opacity: 0.45, marginTop: 4, lineHeight: 1.4 }}>{children}</div>;
}

function TargetField({ label, value, targets, onChange }: {
  label: string; value: string | undefined; targets: string[]; onChange: (v: string | undefined) => void;
}) {
  return (
    <Field label={label}>
      <select value={value || ''} onChange={e => onChange(e.target.value || undefined)} style={input}>
        <option value="">(aucun)</option>
        {targets.map(t => <option key={t} value={t}>{t}</option>)}
        {value && !targets.includes(value) && <option value={value}>{value} (?)</option>}
      </select>
    </Field>
  );
}

function MapField({ label, value, onChange }: {
  label: string; value: Record<string, any>; onChange: (v: Record<string, any>) => void;
}) {
  const entries = Object.entries(value || {});
  const setKey = (oldK: string, newK: string) => {
    const next: Record<string, any> = {};
    for (const [k, v] of entries) next[k === oldK ? newK : k] = v;
    onChange(next);
  };
  const setVal = (k: string, v: string) => onChange({ ...value, [k]: v });
  const remove = (k: string) => { const n = { ...value }; delete n[k]; onChange(n); };
  const add = () => onChange({ ...value, '': '' });
  return (
    <div style={{ marginBottom: 8 }}>
      <label style={{ display: 'block', fontSize: 10, opacity: 0.6, marginBottom: 2 }}>{label}</label>
      {entries.map(([k, v], i) => (
        <div key={i} style={{ display: 'flex', gap: 4, marginBottom: 3 }}>
          <input value={k} onChange={e => setKey(k, e.target.value)} placeholder="clé" style={{ ...input, flex: 1 }} />
          <input value={String(v ?? '')} onChange={e => setVal(k, e.target.value)} placeholder="valeur" style={{ ...input, flex: 1 }} />
          <button onClick={() => remove(k)} style={delBtn}>✕</button>
        </div>
      ))}
      <button onClick={add} style={addBtn}>+ entrée</button>
    </div>
  );
}

function stepToYaml(step: Step): string {
  const obj: Record<string, any> = { id: step.id, type: step.type };
  const keys = new Set([
    'fn','uses_llm','inputs','capture','agent','entrypoint',
    'next','on_error','default','conditions','status',
    'var','items','start','end','step','condition',
    'name','value','tool','args','output_capture',
    'skill_prompt','provider_ref','model_ref','temperature','max_tokens',
    'role','occupation','request','to','duration_seconds',
  ]);
  for (const k of keys) {
    const v = (step as any)[k];
    if (v === undefined || v === null || v === '' || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)) continue;
    obj[k] = v;
  }
  return YAML.stringify([obj], { lineWidth: 0, indent: 2 }).trim();
}

const input: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', background: '#181825', color: '#cdd6f4',
  border: '1px solid #45475a', borderRadius: 5, padding: '4px 6px', fontSize: 12,
  fontFamily: 'inherit',
};
const delBtn: React.CSSProperties = {
  background: 'transparent', color: '#f38ba8', border: '1px solid #45475a',
  borderRadius: 5, padding: '2px 6px', cursor: 'pointer', fontSize: 11,
};
const addBtn: React.CSSProperties = {
  background: '#313244', color: '#a6e3a1', border: 'none',
  borderRadius: 5, padding: '3px 8px', cursor: 'pointer', fontSize: 11, marginBottom: 8,
};
