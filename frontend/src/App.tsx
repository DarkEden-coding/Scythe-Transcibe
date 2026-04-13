import type { CSSProperties, PointerEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { startMicRecording } from "./audio";
import { OpenRouterModelPicker, type OrModel } from "./OpenRouterModelPicker";

type TabId = "general" | "keys" | "transcribe" | "postprocess" | "output";

const TABS: { id: TabId; label: string }[] = [
  { id: "general", label: "General" },
  { id: "keys", label: "API keys" },
  { id: "transcribe", label: "Transcribe" },
  { id: "postprocess", label: "Post-process" },
  { id: "output", label: "Output" },
];

const GROQ_STT_DEFAULTS = [
  "whisper-large-v3",
  "whisper-large-v3-turbo",
  "distil-whisper-large-v3-en",
] as const;

/** Groq ``reasoning_effort`` (model-dependent); empty = API default. */
const GROQ_POST_REASONING_EFFORTS = ["", "none", "default", "low", "medium", "high"] as const;

/** OpenRouter ``reasoning.effort``; empty = omit. */
const OR_POST_REASONING_EFFORTS = ["", "xhigh", "high", "medium", "low", "minimal", "none"] as const;

type AppPreferences = {
  transcription_provider: string;
  transcription_model_groq: string;
  transcription_model_openrouter: string;
  postprocess_enabled: boolean;
  postprocess_prompt: string;
  postprocess_provider: string;
  postprocess_model: string;
  postprocess_groq_reasoning_effort: string;
  postprocess_openrouter_reasoning_effort: string;
  openrouter_models_cache_hint: string;
  keyword_replacement_spec: string;
  openrouter_transcription_instruction: string;
  hotkey_toggle_recording: string;
};

type KeysPublic = {
  groq_configured: boolean;
  openrouter_configured: boolean;
};

type TranscriptionHistoryEntry = {
  id: string;
  createdAt: number;
  transcript: string;
  processed: string;
  transcriptChars: number;
  transcribeMs: number;
  postprocessMs: number | null;
  prePostprocessMs: number | null;
  postprocessPrepMs: number | null;
  postprocessApiMs: number | null;
  postprocessChunks: number | null;
  hotkeyPostApiToPasteMs: number | null;
  hotkeyPasteChordMs: number | null;
  totalMs: number;
};

function numOrNull(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function mapHistoryEntry(raw: Record<string, unknown>): TranscriptionHistoryEntry {
  const createdRaw = raw.created_at ?? raw.createdAt;
  const createdAt =
    typeof createdRaw === "number"
      ? createdRaw
      : typeof createdRaw === "string"
        ? Number(createdRaw)
        : Date.now();
  const idRaw = raw.id;
  const id =
    typeof idRaw === "string" && idRaw.length > 0 ? idRaw : `legacy-${createdAt}`;
  const ppRaw = raw.postprocess_ms ?? raw.postprocessMs;
  return {
    id,
    createdAt: Number.isFinite(createdAt) ? createdAt : Date.now(),
    transcript: String(raw.transcript ?? ""),
    processed: String(raw.processed ?? ""),
    transcriptChars: Number(raw.transcript_chars ?? raw.transcriptChars ?? 0),
    transcribeMs: Number(raw.transcribe_ms ?? raw.transcribeMs ?? 0),
    postprocessMs:
      ppRaw === null || ppRaw === undefined ? null : Number(ppRaw),
    prePostprocessMs: numOrNull(raw.pre_postprocess_ms ?? raw.prePostprocessMs),
    postprocessPrepMs: numOrNull(raw.postprocess_prep_ms ?? raw.postprocessPrepMs),
    postprocessApiMs: numOrNull(raw.postprocess_api_ms ?? raw.postprocessApiMs),
    postprocessChunks: numOrNull(raw.postprocess_chunks ?? raw.postprocessChunks),
    hotkeyPostApiToPasteMs: numOrNull(
      raw.hotkey_post_api_to_paste_ms ?? raw.hotkeyPostApiToPasteMs,
    ),
    hotkeyPasteChordMs: numOrNull(raw.hotkey_paste_chord_ms ?? raw.hotkeyPasteChordMs),
    totalMs: Number(raw.total_ms ?? raw.totalMs ?? 0),
  };
}

function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return "—";
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  return `${(ms / 1000).toFixed(2)} s`;
}

function historyTimingSecondaryLine(e: TranscriptionHistoryEntry): string | null {
  const parts: string[] = [];
  if (e.transcriptChars > 0) {
    parts.push(`${e.transcriptChars.toLocaleString()} chars`);
  }
  if (e.postprocessMs != null && Number.isFinite(e.postprocessMs)) {
    if (e.prePostprocessMs != null) {
      parts.push(`before LLM ${formatDurationMs(e.prePostprocessMs)}`);
    }
    if (e.postprocessPrepMs != null) {
      parts.push(`chunk prep ${formatDurationMs(e.postprocessPrepMs)}`);
    }
    if (e.postprocessApiMs != null) {
      parts.push(`API wall ${formatDurationMs(e.postprocessApiMs)}`);
    }
    if (e.postprocessChunks != null && e.postprocessChunks >= 1) {
      parts.push(
        e.postprocessChunks === 1 ? "1 chunk" : `${e.postprocessChunks} chunks`,
      );
    }
  }
  if (parts.length === 0) return null;
  return parts.join(" · ");
}

function historyTimingHotkeyLine(e: TranscriptionHistoryEntry): string | null {
  if (e.hotkeyPostApiToPasteMs == null && e.hotkeyPasteChordMs == null) return null;
  const p: string[] = [];
  if (e.hotkeyPostApiToPasteMs != null) {
    p.push(`after API → paste ${formatDurationMs(e.hotkeyPostApiToPasteMs)}`);
  }
  if (e.hotkeyPasteChordMs != null) {
    p.push(`paste chord ${formatDurationMs(e.hotkeyPasteChordMs)}`);
  }
  return p.length ? `Hotkey: ${p.join(" · ")}` : null;
}

const defaultPrefs = (): AppPreferences => ({
  transcription_provider: "groq",
  transcription_model_groq: "whisper-large-v3-turbo",
  transcription_model_openrouter: "",
  postprocess_enabled: false,
  postprocess_prompt: "Summarize the transcript in bullet points.",
  postprocess_provider: "openrouter",
  postprocess_model: "openai/gpt-4o-mini",
  postprocess_groq_reasoning_effort: "",
  postprocess_openrouter_reasoning_effort: "",
  openrouter_models_cache_hint: "",
  keyword_replacement_spec: "",
  openrouter_transcription_instruction:
    "Transcribe this audio accurately. Reply with only the transcript.",
  hotkey_toggle_recording: "ctrl+shift+space",
});

const MODIFIER_KEYS = new Set([
  "ctrl",
  "alt",
  "shift",
  "meta",
  "control",
  "os",
  "super",
]);

/** Normalizes `e.key` for the non-modifier slot (Win/OS/Super → meta for consistency). */
function normalizeKeySlot(e: KeyboardEvent): string {
  const raw = e.key;
  if (raw === " ") return "space";
  const lower = raw.length === 1 ? raw.toLowerCase() : raw.toLowerCase();
  if (lower === "os" || lower === "super") return "meta";
  return lower;
}

function normalizeHotkeyFromEvent(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.ctrlKey) parts.push("ctrl");
  if (e.altKey) parts.push("alt");
  if (e.shiftKey) parts.push("shift");
  if (e.metaKey) parts.push("meta");

  const keySlot = normalizeKeySlot(e);
  const isDuplicateModifierSlot =
    (keySlot === "meta" && e.metaKey) ||
    (keySlot === "control" && e.ctrlKey) ||
    (keySlot === "shift" && e.shiftKey) ||
    (keySlot === "alt" && e.altKey);
  if (!isDuplicateModifierSlot) {
    parts.push(keySlot);
  }
  return parts.join("+");
}

function isOnlyModifiersCombo(combo: string): boolean {
  return combo.split("+").every((p) => MODIFIER_KEYS.has(p));
}

function formatHotkeyLabel(combo: string): string {
  const t = combo.trim();
  if (!t) return "None";
  const metaName = /Mac|iPhone|iPad|iPod/i.test(
    typeof navigator !== "undefined" ? navigator.platform : "",
  )
    ? "Cmd"
    : "Win";
  return t
    .split("+")
    .map((p) => {
      if (p === "ctrl") return "Ctrl";
      if (p === "meta" || p === "os" || p === "super") return metaName;
      if (p === "alt") return "Alt";
      if (p === "shift") return "Shift";
      if (p === "space") return "Space";
      if (p.length === 1) return p.toUpperCase();
      return p.charAt(0).toUpperCase() + p.slice(1);
    })
    .join("+");
}

/** Matches `text_replacements.parse_replacement_spec` (first arrow only). */
const KEYWORD_LINE =
  /^(.*?)\s*(?:->|=>|→|⇒|\t)\s*(.*)$/s;

type KeywordRow = { id: string; from: string; to: string };

function parseKeywordPairs(spec: string): KeywordRow[] {
  const out: KeywordRow[] = [];
  for (const rawLine of spec.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(KEYWORD_LINE);
    if (!m || m[1] === undefined || m[2] === undefined) continue;
    const from = m[1].trim();
    const to = m[2].trim();
    if (!from) continue;
    out.push({ id: crypto.randomUUID(), from, to });
  }
  return out;
}

function serializeKeywordPairs(rows: KeywordRow[]): string {
  const lines: string[] = [];
  for (const r of rows) {
    const from = r.from.trim();
    if (!from) continue;
    lines.push(`${from} -> ${r.to.trim()}`);
  }
  return lines.join("\n");
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
    },
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || r.statusText);
  }
  return r.json() as Promise<T>;
}

export function App() {
  const [prefs, setPrefs] = useState<AppPreferences>(defaultPrefs);
  const [keys, setKeys] = useState<KeysPublic>({
    groq_configured: false,
    openrouter_configured: false,
  });
  const [groqKeyInput, setGroqKeyInput] = useState("");
  const [orKeyInput, setOrKeyInput] = useState("");
  const [orModels, setOrModels] = useState<OrModel[]>([]);
  const [groqChatModels, setGroqChatModels] = useState<string[]>([]);
  const [transcriptionHistory, setTranscriptionHistory] = useState<
    TranscriptionHistoryEntry[]
  >([]);
  const [status, setStatus] = useState("Idle");
  const [statusColor, setStatusColor] = useState("#666666");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const micRef = useRef<{ stop: () => Promise<Blob> } | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>("keys");
  const [bgPos, setBgPos] = useState({ x: 50, y: 40 });
  const [capturingToggleRecording, setCapturingToggleRecording] = useState(false);
  const [keywordRows, setKeywordRows] = useState<KeywordRow[]>([]);
  const [startupEnabled, setStartupEnabled] = useState(false);
  const [startupLoading, setStartupLoading] = useState(false);
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;

  const onShellPointerMove = useCallback((e: PointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    const x = ((e.clientX - r.left) / Math.max(r.width, 1)) * 100;
    const y = ((e.clientY - r.top) / Math.max(r.height, 1)) * 100;
    setBgPos({ x, y });
  }, []);

  const setPref = useCallback(<K extends keyof AppPreferences>(k: K, v: AppPreferences[K]) => {
    setPrefs((p) => ({ ...p, [k]: v }));
  }, []);

  const commitKeywordRows = useCallback(
    (nextOrFn: KeywordRow[] | ((prev: KeywordRow[]) => KeywordRow[])) => {
      setKeywordRows((prev) => (typeof nextOrFn === "function" ? nextOrFn(prev) : nextOrFn));
    },
    [],
  );

  useEffect(() => {
    if (!hydrated) return;
    setPref("keyword_replacement_spec", serializeKeywordPairs(keywordRows));
  }, [keywordRows, hydrated, setPref]);

  useEffect(() => {
    if (activeTab !== "output" || !hydrated) return;
    const refresh = async () => {
      try {
        const hist = await apiJson<{ entries: Record<string, unknown>[] }>(
          "/api/transcription-history",
        );
        setTranscriptionHistory((hist.entries ?? []).map(mapHistoryEntry));
      } catch {
        /* offline */
      }
    };
    void refresh();
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [activeTab, hydrated]);

  useEffect(() => {
    void (async () => {
      try {
        const [p, k] = await Promise.all([
          apiJson<Record<string, unknown>>("/api/preferences"),
          apiJson<KeysPublic>("/api/keys"),
        ]);
        const merged = { ...defaultPrefs(), ...p } as AppPreferences;
        setPrefs(merged);
        setKeywordRows(parseKeywordPairs(merged.keyword_replacement_spec));
        setKeys(k);
        try {
          const hist = await apiJson<{ entries: Record<string, unknown>[] }>(
            "/api/transcription-history",
          );
          setTranscriptionHistory((hist.entries ?? []).map(mapHistoryEntry));
        } catch {
          /* keep empty history if endpoint unavailable */
        }
        const or = await apiJson<{ models: OrModel[] }>("/api/openrouter/models");
        setOrModels(or.models ?? []);
        if (k.groq_configured) {
          const gm = await apiJson<{ models: string[] }>("/api/groq/chat-models");
          setGroqChatModels(gm.models ?? []);
        }
        try {
          const su = await apiJson<{ enabled: boolean }>("/api/startup");
          setStartupEnabled(su.enabled);
        } catch {
          /* startup endpoint may not be supported on this platform */
        }
      } catch (e) {
        setStatus(`Load failed: ${e instanceof Error ? e.message : String(e)}`);
        setStatusColor("#c62828");
      } finally {
        setHydrated(true);
      }
    })();
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    const t = window.setTimeout(() => {
      void (async () => {
        try {
          await apiJson("/api/preferences", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(prefsRef.current),
          });
        } catch {
          /* ignore autosave errors */
        }
      })();
    }, 500);
    return () => window.clearTimeout(t);
  }, [prefs, hydrated]);

  const saveKeys = async () => {
    const body: { groq?: string | null; openrouter?: string | null } = {};
    if (groqKeyInput.trim()) body.groq = groqKeyInput.trim();
    if (orKeyInput.trim()) body.openrouter = orKeyInput.trim();
    if (!body.groq && !body.openrouter) {
      setStatus("Enter a key to save.");
      setStatusColor("#ff9800");
      return;
    }
    try {
      const k = await apiJson<KeysPublic>("/api/keys", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setKeys(k);
      setGroqKeyInput("");
      setOrKeyInput("");
      setStatus("Keys saved.");
      setStatusColor("#66bb6a");
      if (k.groq_configured) {
        const gm = await apiJson<{ models: string[] }>("/api/groq/chat-models");
        setGroqChatModels(gm.models ?? []);
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
      setStatusColor("#c62828");
    }
  };

  const toggleStartup = async (enabled: boolean) => {
    setStartupLoading(true);
    try {
      const res = await apiJson<{ enabled: boolean }>("/api/startup", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      setStartupEnabled(res.enabled);
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
      setStatusColor("#c62828");
    } finally {
      setStartupLoading(false);
    }
  };

  const refreshOrModels = async () => {
    try {
      setStatus("Loading OpenRouter models…");
      setStatusColor("#ff9800");
      const res = await apiJson<{ models: OrModel[] }>("/api/openrouter/models/refresh", {
        method: "POST",
      });
      setOrModels(res.models ?? []);
      setStatus(`Loaded ${res.models?.length ?? 0} models.`);
      setStatusColor("#66bb6a");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
      setStatusColor("#c62828");
    }
  };

  const onRecordClick = useCallback(async () => {
    if (busy) return;
    if (!recording) {
      try {
        const session = await startMicRecording();
        micRef.current = session;
        setRecording(true);
        setStatus("Recording…");
        setStatusColor("#e53935");
      } catch (e) {
        setStatus(e instanceof Error ? e.message : String(e));
        setStatusColor("#c62828");
      }
      return;
    }
    const session = micRef.current;
    micRef.current = null;
    setRecording(false);
    if (!session) return;
    setBusy(true);
    setStatus("Transcribing…");
    setStatusColor("#ff9800");
    try {
      const blob = await session.stop();
      const p = prefsRef.current;
      const meta = {
        transcription_provider: p.transcription_provider,
        transcription_model_groq: p.transcription_model_groq,
        transcription_model_openrouter: p.transcription_model_openrouter,
        openrouter_transcription_instruction: p.openrouter_transcription_instruction,
        keyword_replacement_spec: p.keyword_replacement_spec,
        postprocess_enabled: p.postprocess_enabled,
        postprocess_prompt: p.postprocess_prompt,
        postprocess_provider: p.postprocess_provider,
        postprocess_model: p.postprocess_model,
        postprocess_groq_reasoning_effort: p.postprocess_groq_reasoning_effort,
        postprocess_openrouter_reasoning_effort: p.postprocess_openrouter_reasoning_effort,
      };
      const fd = new FormData();
      fd.set("meta", JSON.stringify(meta));
      fd.set("audio", blob, "recording.wav");
      const r = await fetch("/api/transcribe", { method: "POST", body: fd });
      if (!r.ok) {
        throw new Error(await r.text());
      }
      const raw = (await r.json()) as Record<string, unknown>;
      const entry = mapHistoryEntry(raw);
      setTranscriptionHistory((prev) => [
        entry,
        ...prev.filter((e) => e.id !== entry.id),
      ]);
      setStatus("Idle");
      setStatusColor("#666666");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
      setStatusColor("#c62828");
    } finally {
      setBusy(false);
    }
  }, [busy, recording]);

  useEffect(() => {
    if (!capturingToggleRecording) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        setCapturingToggleRecording(false);
        return;
      }
      const combo = normalizeHotkeyFromEvent(e);
      if (isOnlyModifiersCombo(combo)) return;
      setPref("hotkey_toggle_recording", combo);
      setCapturingToggleRecording(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [capturingToggleRecording, setPref]);

  const isGroq = prefs.transcription_provider === "groq";
  const postGroq = prefs.postprocess_provider === "groq";

  const groqModelInDefaults = GROQ_STT_DEFAULTS.includes(
    prefs.transcription_model_groq as (typeof GROQ_STT_DEFAULTS)[number],
  );
  const groqDropdownValue = groqModelInDefaults
    ? prefs.transcription_model_groq
    : GROQ_STT_DEFAULTS[1];
  const groqCustomValue = groqModelInDefaults ? "" : prefs.transcription_model_groq;

  const bgStyle = {
    "--px": `${bgPos.x}%`,
    "--py": `${bgPos.y}%`,
  } as CSSProperties;

  return (
    <div className="app-shell" onPointerMove={onShellPointerMove}>
      <div className="app-bg" style={bgStyle} aria-hidden />
      <div className="app-inner">
        <header className="app-header">
          <h1 className="app-title">Scythe-Transcribe</h1>
          <div style={{ flex: 1 }} />
          <div className="status-pill">
            <span
              className="status-dot"
              style={{ background: statusColor, color: statusColor }}
            />
            <span>{status}</span>
          </div>
        </header>

        <div className="app-card">
          <div
            className="tablist"
            role="tablist"
            aria-label="Settings sections"
          >
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`tab-${t.id}`}
                aria-selected={activeTab === t.id}
                aria-controls={`panel-${t.id}`}
                className="tab"
                onClick={() => setActiveTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>

          {activeTab === "general" && (
          <div
            id="panel-general"
            role="tabpanel"
            aria-labelledby="tab-general"
          >
            <h2 className="section-title">Keyboard shortcuts</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              The desktop process captures this shortcut globally (hold to record, release to
              transcribe, post-process if enabled, then paste at the text cursor). Keep the settings
              server running from the tray app. Win/Meta combos may still be taken by the OS before
              Scythe sees them. Press Esc to cancel capture.
            </p>
            <div className="field-row" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
              <label className="flex-240">
                Hold to dictate (paste)
                <input
                  type="text"
                  className="input-field"
                  readOnly
                  value={
                    capturingToggleRecording
                      ? "Press keys…"
                      : formatHotkeyLabel(prefs.hotkey_toggle_recording)
                  }
                  aria-live="polite"
                />
              </label>
              <button
                type="button"
                className={capturingToggleRecording ? "btn-primary" : "btn-outline"}
                onClick={() => setCapturingToggleRecording((c) => !c)}
              >
                {capturingToggleRecording ? "Cancel capture" : "Set shortcut"}
              </button>
              <button
                type="button"
                className="btn-outline"
                disabled={!prefs.hotkey_toggle_recording.trim()}
                onClick={() => setPref("hotkey_toggle_recording", "")}
              >
                Clear
              </button>
              <button
                type="button"
                className="btn-outline"
                onClick={() =>
                  setPref("hotkey_toggle_recording", defaultPrefs().hotkey_toggle_recording)
                }
              >
                Reset to default
              </button>
            </div>

            <h2 className="section-title" style={{ marginTop: "1.5rem" }}>Startup</h2>
            <div className="field-row" style={{ alignItems: "center" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={startupEnabled}
                  disabled={startupLoading}
                  onChange={(e) => void toggleStartup(e.target.checked)}
                />
                Launch at login
              </label>
              <span className="muted" style={{ marginLeft: "0.5rem" }}>
                Automatically start Scythe-Transcribe when you log in.
              </span>
            </div>
          </div>
          )}

          {activeTab === "keys" && (
          <div
            id="panel-keys"
            role="tabpanel"
            aria-labelledby="tab-keys"
          >
            <h2 className="section-title">API keys (stored in local file)</h2>
            <div className="field-row">
              <label className="flex-240">
                Groq API key
                <input
                  type="password"
                  className="input-field"
                  value={groqKeyInput}
                  onChange={(e) => setGroqKeyInput(e.target.value)}
                  placeholder={keys.groq_configured ? "(saved)" : ""}
                  autoComplete="off"
                />
              </label>
              <label className="flex-240">
                OpenRouter API key
                <input
                  type="password"
                  className="input-field"
                  value={orKeyInput}
                  onChange={(e) => setOrKeyInput(e.target.value)}
                  placeholder={keys.openrouter_configured ? "(saved)" : ""}
                  autoComplete="off"
                />
              </label>
              <button type="button" className="btn-primary" onClick={() => void saveKeys()}>
                Save keys
              </button>
              <button type="button" className="btn-outline" onClick={() => void refreshOrModels()}>
                Refresh OpenRouter models
              </button>
            </div>
            <p className="muted">
              Groq: {keys.groq_configured ? "key saved" : "no key"} · OpenRouter:{" "}
              {keys.openrouter_configured ? "key saved" : "no key"}
            </p>
          </div>
          )}

          {activeTab === "transcribe" && (
          <div
            id="panel-transcribe"
            role="tabpanel"
            aria-labelledby="tab-transcribe"
          >
            <div className="stack-gap">
              <div>
                <h2 className="section-title">Transcription</h2>
                <label>
                  Provider
                  <select
                    className="input-field"
                    style={{ width: 220 }}
                    value={prefs.transcription_provider}
                    onChange={(e) => setPref("transcription_provider", e.target.value)}
                  >
                    <option value="groq">Groq</option>
                    <option value="openrouter">OpenRouter</option>
                  </select>
                </label>
              </div>

              {isGroq ? (
                <>
                  <div className="field-row">
                    <label>
                      Groq model
                      <select
                        className="input-field"
                        style={{ width: 300 }}
                        value={groqDropdownValue}
                        onChange={(e) => setPref("transcription_model_groq", e.target.value)}
                      >
                        {GROQ_STT_DEFAULTS.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label style={{ flex: 1, minWidth: 200 }}>
                      Custom Groq model id (optional)
                      <input
                        className="input-field"
                        value={groqCustomValue}
                        onChange={(e) => {
                          const v = e.target.value.trim();
                          setPref("transcription_model_groq", v || groqDropdownValue);
                        }}
                        placeholder="Overrides dropdown when set"
                      />
                    </label>
                  </div>
                </>
              ) : (
                <>
                  <OpenRouterModelPicker
                    idBase="or-transcribe"
                    label="OpenRouter model (audio input)"
                    models={orModels}
                    mode="audio"
                    value={prefs.transcription_model_openrouter}
                    onChange={(id) => setPref("transcription_model_openrouter", id)}
                  />
                  <label>
                    Custom OpenRouter model id
                    <input
                      className="input-field"
                      value={prefs.transcription_model_openrouter}
                      onChange={(e) => setPref("transcription_model_openrouter", e.target.value)}
                    />
                  </label>
                  <label>
                    OpenRouter transcription instruction
                    <textarea
                      className="input-field"
                      style={{ minHeight: 72 }}
                      value={prefs.openrouter_transcription_instruction}
                      onChange={(e) =>
                        setPref("openrouter_transcription_instruction", e.target.value)
                      }
                    />
                  </label>
                </>
              )}

              <div>
                <h2 className="section-title">Keyword dictionary</h2>
                <p className="muted" style={{ marginTop: 0 }}>
                  Replace mistaken words or phrases in the raw transcript (before any LLM step). Longer
                  phrases are applied first. With Groq transcription, these terms are also sent as a
                  Whisper prompt so recognition can follow your vocabulary.
                </p>
                {keywordRows.length === 0 ? (
                  <p className="muted keyword-dict-empty">No rules yet. Add a replacement below.</p>
                ) : (
                  <ul className="keyword-list" aria-label="Keyword replacements">
                    {keywordRows.map((row, index) => (
                      <li key={row.id} className="keyword-row">
                        <label className="keyword-field">
                          <span className="keyword-field-label">Find</span>
                          <input
                            className="input-field"
                            value={row.from}
                            onChange={(e) => {
                              const v = e.target.value;
                              commitKeywordRows((prev) =>
                                prev.map((r) => (r.id === row.id ? { ...r, from: v } : r)),
                              );
                            }}
                            placeholder="e.g. teh"
                            autoComplete="off"
                            spellCheck={true}
                          />
                        </label>
                        <span className="keyword-arrow" aria-hidden>
                          →
                        </span>
                        <label className="keyword-field">
                          <span className="keyword-field-label">Replace with</span>
                          <input
                            className="input-field"
                            value={row.to}
                            onChange={(e) => {
                              const v = e.target.value;
                              commitKeywordRows((prev) =>
                                prev.map((r) => (r.id === row.id ? { ...r, to: v } : r)),
                              );
                            }}
                            placeholder="e.g. the"
                            autoComplete="off"
                            spellCheck={true}
                          />
                        </label>
                        <button
                          type="button"
                          className="btn-remove-row"
                          onClick={() =>
                            commitKeywordRows((prev) => prev.filter((r) => r.id !== row.id))
                          }
                          aria-label={`Remove replacement ${index + 1}`}
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="keyword-dict-actions">
                  <button
                    type="button"
                    className="btn-outline"
                    onClick={() =>
                      commitKeywordRows((prev) => [
                        ...prev,
                        { id: crypto.randomUUID(), from: "", to: "" },
                      ])
                    }
                  >
                    Add rule
                  </button>
                </div>
                <p className="muted">
                  Lines starting with # in a saved file are treated as comments by the engine; this
                  editor only manages replacement rules.
                </p>
              </div>

              <div>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={busy}
                  onClick={() => void onRecordClick()}
                >
                  {recording ? "Stop & transcribe" : "Start recording"}
                </button>
              </div>
            </div>
          </div>
          )}

          {activeTab === "postprocess" && (
          <div
            id="panel-postprocess"
            role="tabpanel"
            aria-labelledby="tab-postprocess"
          >
            <h2 className="section-title">LLM post-processing</h2>
            <div className="stack-gap">
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={prefs.postprocess_enabled}
                  onChange={(e) => setPref("postprocess_enabled", e.target.checked)}
                />
                Auto-process transcript with prompt
              </label>
              <label>
                Post-process prompt
                <textarea
                  className="input-field"
                  style={{ minHeight: 80 }}
                  value={prefs.postprocess_prompt}
                  onChange={(e) => setPref("postprocess_prompt", e.target.value)}
                />
              </label>
              <label>
                Post-process provider
                <select
                  className="input-field"
                  style={{ width: 220 }}
                  value={prefs.postprocess_provider}
                  onChange={(e) => setPref("postprocess_provider", e.target.value)}
                >
                  <option value="groq">Groq</option>
                  <option value="openrouter">OpenRouter</option>
                </select>
              </label>

              {postGroq ? (
                <label>
                  Groq post-process model
                  <select
                    className="input-field"
                    value={prefs.postprocess_model}
                    onChange={(e) => setPref("postprocess_model", e.target.value)}
                  >
                    <option value="">(select)</option>
                    {groqChatModels.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <OpenRouterModelPicker
                  idBase="or-postprocess"
                  label="OpenRouter post-process model"
                  models={orModels}
                  mode="all"
                  value={prefs.postprocess_model}
                  onChange={(id) => setPref("postprocess_model", id)}
                />
              )}
              <label>
                Custom post-process model id (optional override)
                <input
                  className="input-field"
                  value={prefs.postprocess_model}
                  onChange={(e) => setPref("postprocess_model", e.target.value)}
                />
              </label>
              {postGroq ? (
                <label>
                  Groq reasoning effort
                  <select
                    className="input-field"
                    style={{ width: 220 }}
                    value={prefs.postprocess_groq_reasoning_effort}
                    onChange={(e) => setPref("postprocess_groq_reasoning_effort", e.target.value)}
                  >
                    <option value="">Default (omit)</option>
                    {GROQ_POST_REASONING_EFFORTS.filter((x) => x !== "").map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label>
                  OpenRouter reasoning effort
                  <select
                    className="input-field"
                    style={{ width: 220 }}
                    value={prefs.postprocess_openrouter_reasoning_effort}
                    onChange={(e) =>
                      setPref("postprocess_openrouter_reasoning_effort", e.target.value)
                    }
                  >
                    <option value="">Default (omit)</option>
                    {OR_POST_REASONING_EFFORTS.filter((x) => x !== "").map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <p className="muted">
                Reasoning options depend on the post-process model; unsupported values may be
                ignored or rejected by the provider.
              </p>
            </div>
          </div>
          )}

          {activeTab === "output" && (
          <div
            id="panel-output"
            role="tabpanel"
            aria-labelledby="tab-output"
          >
            <h2 className="section-title">Transcription history</h2>
            <p className="muted output-hint">
              Timings below include pipeline breakdown; the Output tab refreshes every 2s so hotkey
              paste metrics appear after dictation.
            </p>
            {transcriptionHistory.length === 0 ? (
              <p className="muted">No transcriptions yet. Record and transcribe from the Transcribe tab.</p>
            ) : (
              <ul className="history-list" aria-label="Transcription history">
                {transcriptionHistory.map((entry) => {
                  const timingExtra = historyTimingSecondaryLine(entry);
                  const timingHotkey = historyTimingHotkeyLine(entry);
                  return (
                  <li key={entry.id} className="history-item">
                    <time className="history-time" dateTime={new Date(entry.createdAt).toISOString()}>
                      {new Date(entry.createdAt).toLocaleString()}
                    </time>
                    <p className="history-metrics" aria-label="Timing">
                      Transcribe {formatDurationMs(entry.transcribeMs)}
                      {entry.postprocessMs != null && Number.isFinite(entry.postprocessMs)
                        ? ` · Post-process ${formatDurationMs(entry.postprocessMs)}`
                        : ""}
                      {` · Total ${formatDurationMs(entry.totalMs)}`}
                    </p>
                    {timingExtra ? (
                      <p className="history-timing-detail" aria-label="Pipeline timing detail">
                        {timingExtra}
                      </p>
                    ) : null}
                    {timingHotkey ? (
                      <p className="history-timing-detail" aria-label="Hotkey timing">
                        {timingHotkey}
                      </p>
                    ) : null}
                    <div className="history-item-body">
                      <label className="history-block">
                        Transcript
                        <textarea
                          className="input-field history-textarea"
                          readOnly
                          value={entry.transcript}
                          rows={6}
                        />
                      </label>
                      <label className="history-block">
                        LLM processed output
                        <textarea
                          className="input-field history-textarea"
                          readOnly
                          value={entry.processed}
                          rows={6}
                          placeholder="(none)"
                        />
                      </label>
                    </div>
                  </li>
                  );
                })}
              </ul>
            )}
          </div>
          )}
        </div>
      </div>
    </div>
  );
}
