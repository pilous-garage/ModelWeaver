import React, { useMemo, useState } from 'react';
import { usePoll, unwrapResult } from '../panels/panel-utils.ts';
import { TripleCheckbox, type TriState } from './TripleCheckbox.tsx';
import { CustomSelect } from './CustomSelect.tsx';

/**
 * ModelPicker — sélection provider/modèle avec FILTRES d'exclusion.
 *
 * La liste des modèles vient du daemon (llm/models/list). Chaque modèle porte
 * des flags de catalogue (status, modality, is_open_weights, license,
 * architecture, developer, target_use…).
 *
 * Filtres :
 *   - booléens (is_open_weights) → triple-checkbox (vide / vert+ / rouge−).
 *   - énumérés (modality, target_use, license…) → multi-select (valeurs
 *     retenues OU exclues).
 * Le tri s'applique sur TOUT ; seuls les providers ayant ≥1 modèle
 * correspondant restent affichés. Container « avancé » pour les flags
 * secondaires.
 */

interface ModelDef { ref: string; name?: string; provider_ref: string; [k: string]: any }

/** Label lisible d'un modèle : le dernier segment du ref (ex. claude-opus-5),
 *  car certains providers (ex. opencode-zen) mettent `name` = provider partout. */
function modelLabel(m: ModelDef): string {
  const seg = (m.ref ?? '').split('/').pop();
  return seg || m.name || m.ref;
}

interface Filter { include: Set<string>; exclude: Set<string> }
type BoolFilters = Record<string, TriState>;

function parseValue(v: any): string {
  if (v == null) return '';
  return String(v);
}

export function ModelPicker(props: {
  provider: string;
  model: string;
  onProvider(p: string): void;
  onModel(m: string): void;
  api: { post(r: string, b?: any): Promise<any> };
  t?: (k: string) => string;
}) {
  const { provider, model, onProvider, onModel, api, t } = props;

  const models = usePoll<any>(api.post, 'llm/models/list', {}, 60000,
    (res) => unwrapResult(res).models ?? [], true);

  const [showAdvanced, setShowAdvanced] = useState(false);
  // filtres booléens (is_open_weights…) et énumérés (modality, target_use…)
  const [boolF, setBoolF] = useState<BoolFilters>({});
  const [enumF, setEnumF] = useState<Record<string, Filter>>({});

  // Catégorisation des flags : booléens vs énumérés (à partir des données)
  const { boolKeys, enumKeys } = useMemo(() => {
    const bool: string[] = [];
    const enums: string[] = [];
    const sample: ModelDef[] = models.data ?? [];
    const sampleKeys = sample.length ? Object.keys(sample[0]) : [];
    const interesting = ['is_open_weights', 'modality', 'target_use', 'license', 'architecture', 'developer', 'status', 'parameter_count'];
    const check = ['is_open_weights'];
    for (const k of sampleKeys) {
      if (!interesting.includes(k)) continue;
      if (check.includes(k)) { bool.push(k); continue; }
      const vals = new Set(sample.map((m) => parseValue(m[k])));
      enums.push(k);
    }
    return { boolKeys: bool, enumKeys: enums };
  }, [models.data]);

  // Application des filtres
  const filteredModels = useMemo(() => {
    const list = (models.data ?? []) as ModelDef[];
    return list.filter((m) => {
      for (const k of boolKeys) {
        const f = boolF[k];
        if (!f) continue;
        const v = parseValue(m[k]);
        const truthy = v === '1' || v === 'true';
        if (f === 'yes' && !truthy) return false;
        if (f === 'no' && truthy) return false;
      }
      for (const k of enumKeys) {
        const f = enumF[k];
        if (!f) continue;
        const v = parseValue(m[k]);
        if (f.include.size && !f.include.has(v)) return false;
        if (f.exclude.has(v)) return false;
      }
      return true;
    });
  }, [models.data, boolF, enumF, boolKeys, enumKeys]);

  // Providers ayant ≥1 modèle après filtrage
  const byProvider = useMemo(() => {
    const m: Record<string, ModelDef[]> = {};
    for (const x of filteredModels) {
      const p = x.provider_ref ?? '?';
      (m[p] = m[p] || []).push(x);
    }
    return m;
  }, [filteredModels]);
  const providers = Object.keys(byProvider).sort();

  const enumOptions = (k: string): string[] => {
    const list = (models.data ?? []) as ModelDef[];
    const s = new Set<string>(list.map((m) => parseValue(m[k])));
    return [...s].filter(Boolean).sort();
  };

  const toggleEnum = (k: string, v: string, s: TriState) => {
    setEnumF((prev) => {
      const cur = prev[k] ?? { include: new Set<string>(), exclude: new Set<string>() };
      const next = { include: new Set(cur.include), exclude: new Set(cur.exclude) };
      if (s === 'off') {
        next.include.delete(v);
        next.exclude.delete(v);
      } else if (s === 'yes') {
        next.include.add(v);
        next.exclude.delete(v);
      } else {
        next.exclude.add(v);
        next.include.delete(v);
      }
      return { ...prev, [k]: next };
    });
  };

  const lbl = (k: string) => (t ? t(`panels.communication-dev-chat.flag.${k}`) : k);

  // Flags essentiels (simples) vs avancés (container)
  const essentialEnums = ['modality', 'target_use', 'status'];
  const advancedEnums = enumKeys.filter((k) => !essentialEnums.includes(k));

  return (
    <div style={{ fontSize: 11 }}>
      {/* Ligne provider + modèle */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
        <CustomSelect
          testid="dev-chat-provider"
          value={provider}
          onChange={(p) => { onProvider(p); onModel(''); }}
          placeholder={t?.('panels.communication-dev-chat.auto') ?? 'Auto'}
          options={providers.map((p) => ({ value: p, label: p, hint: String(byProvider[p].length) }))}
        />
        <CustomSelect
          testid="dev-chat-model"
          value={model}
          onChange={onModel}
          disabled={!provider}
          placeholder={t?.('panels.communication-dev-chat.modele') ?? 'Modèle'}
          maxWidth={260}
          options={(byProvider[provider] ?? []).map((m) => ({ value: m.provider_model_name ?? m.ref, label: modelLabel(m) }))}
        />
        <button onClick={() => setShowAdvanced((v) => !v)}
          style={{ fontSize: 10, padding: '1px 8px', cursor: 'pointer', background: 'transparent', color: '#94a3b8', border: '1px solid var(--mw-border, #334155)', borderRadius: 4 }}>
          {showAdvanced ? '▲' : '⋯'}
        </button>
      </div>

      {/* Filtres booléens (triple-checkbox) */}
      {boolKeys.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
          {boolKeys.map((k) => (
            <label key={k} style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#94a3b8' }}>
              <TripleCheckbox value={boolF[k] ?? 'off'} onChange={(v) => setBoolF((p) => ({ ...p, [k]: v }))} testid={`dev-chat-f-${k}`} />
              {lbl(k)}
            </label>
          ))}
        </div>
      )}

      {/* Filtres énumérés essentiels */}
      {essentialEnums.map((k) => {
        if (!enumKeys.includes(k)) return null;
        const opts = enumOptions(k);
        const f = enumF[k] ?? { include: new Set<string>(), exclude: new Set<string>() };
        return (
          <div key={k} style={{ marginTop: 3, color: '#94a3b8' }}>
            <span>{lbl(k)} :</span>
            {opts.map((v) => (
              <label key={v} style={{ display: 'inline-flex', alignItems: 'center', gap: 2, marginLeft: 6, color: '#94a3b8', cursor: 'pointer' }}>
                <TripleCheckbox value={f.include.has(v) ? 'yes' : f.exclude.has(v) ? 'no' : 'off'}
                  onChange={(s) => toggleEnum(k, v, s)}
                  testid={`dev-chat-e-${k}-${v}`} />
                {v || '—'}
              </label>
            ))}
          </div>
        );
      })}

      {/* Filtres avancés */}
      {showAdvanced && advancedEnums.length > 0 && (
        <div style={{ marginTop: 4, border: '1px dashed var(--mw-border, #334155)', borderRadius: 6, padding: 4 }}>
          {advancedEnums.map((k) => {
            const opts = enumOptions(k);
            const f = enumF[k] ?? { include: new Set<string>(), exclude: new Set<string>() };
            return (
              <div key={k} style={{ marginTop: 3, color: '#94a3b8' }}>
                <span>{lbl(k)} :</span>
                {opts.slice(0, 12).map((v) => (
                  <label key={v} style={{ display: 'inline-flex', alignItems: 'center', gap: 2, marginLeft: 6, color: '#94a3b8', cursor: 'pointer' }}>
                    <TripleCheckbox value={f.include.has(v) ? 'yes' : f.exclude.has(v) ? 'no' : 'off'}
                      onChange={(s) => toggleEnum(k, v, s)} />
                    {v || '—'}
                  </label>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
