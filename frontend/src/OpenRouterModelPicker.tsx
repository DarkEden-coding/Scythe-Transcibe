import { useCallback, useMemo, useState } from "react";

export type OrModel = {
  model_id: string;
  name: string;
  supports_audio_input: boolean;
  supports_text: boolean;
  pricing_prompt?: string;
  pricing_completion?: string;
};

type OpenRouterModelPickerProps = {
  models: OrModel[];
  value: string;
  onChange: (modelId: string) => void;
  mode: "audio" | "all";
  label: string;
  idBase: string;
};

function priceSummary(m: OrModel): string {
  const a = (m.pricing_prompt ?? "").trim();
  const b = (m.pricing_completion ?? "").trim();
  if (a && b) return `in ${a} · out ${b}`;
  if (a) return `in ${a}`;
  if (b) return `out ${b}`;
  return "";
}

export function OpenRouterModelPicker({
  models,
  value,
  onChange,
  mode,
  label,
  idBase,
}: OpenRouterModelPickerProps) {
  const [query, setQuery] = useState("");

  const baseList = useMemo(() => {
    if (mode === "audio") {
      return models.filter((m) => m.supports_audio_input);
    }
    return models;
  }, [models, mode]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return baseList;
    return baseList.filter((m) => {
      const id = m.model_id.toLowerCase();
      const name = (m.name ?? "").toLowerCase();
      const price = priceSummary(m).toLowerCase();
      return id.includes(q) || name.includes(q) || price.includes(q);
    });
  }, [baseList, query]);

  const selected = useMemo(
    () => baseList.find((m) => m.model_id === value) ?? null,
    [baseList, value],
  );

  const onPick = useCallback(
    (id: string) => {
      onChange(id);
      setQuery("");
    },
    [onChange],
  );

  const searchId = `${idBase}-search`;
  const listId = `${idBase}-listbox`;

  return (
    <div className="or-picker">
      <label className="or-picker-label" htmlFor={searchId}>
        {label}
      </label>
      {selected && (
        <div className="or-picker-selected">
          <span className="or-picker-selected-name">{selected.name || selected.model_id}</span>
          <span className="or-picker-selected-id">{selected.model_id}</span>
          {priceSummary(selected) ? (
            <span className="or-picker-selected-price">{priceSummary(selected)}</span>
          ) : null}
        </div>
      )}
      <input
        id={searchId}
        type="search"
        className="input-field or-picker-search"
        placeholder="Search by name, model id, or price…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoComplete="off"
        aria-controls={listId}
      />
      <div
        id={listId}
        className="or-picker-list"
        role="listbox"
        aria-label={label}
      >
        {filtered.length === 0 ? (
          <div className="or-picker-empty">
            {baseList.length === 0
              ? mode === "audio"
                ? "No audio-capable models in the catalog. Try refreshing the model list from API keys."
                : "No models loaded. Open the API keys tab and use Refresh OpenRouter models."
              : "No models match your search."}
          </div>
        ) : (
          filtered.map((m) => {
            const active = m.model_id === value;
            const p = priceSummary(m);
            return (
              <button
                key={m.model_id}
                type="button"
                role="option"
                aria-selected={active}
                className={`or-picker-row${active ? " or-picker-row-active" : ""}`}
                onClick={() => onPick(m.model_id)}
              >
                <span className="or-picker-row-main">
                  <span className="or-picker-row-name">{m.name || m.model_id}</span>
                  <span className="or-picker-row-id">{m.model_id}</span>
                </span>
                {p ? <span className="or-picker-row-price">{p}</span> : null}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
