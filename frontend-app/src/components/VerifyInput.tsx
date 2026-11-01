import React, { useEffect, useRef, useState } from "react";
import { PipelineError, verifyInput, type EvidenceMode } from "../lib/api";
import type { Report } from "../types";

interface Props {
  onReportReady: (report: Report) => void;
  onCancel?: () => void;
  compact?: boolean;
}

const PRESETS = [
  {
    label: "Apollo 11 Mission",
    mode: "text" as const,
    text: "Neil Armstrong and Buzz Aldrin landed the Apollo 11 Lunar Module Eagle on the Moon on July 20, 1969. Armstrong spent about two hours and 15 minutes outside the spacecraft. The mission returned 21.5 kilograms of lunar material to Earth.",
  },
  {
    label: "Eiffel Tower Paris",
    mode: "text" as const,
    text: "The Eiffel Tower was completed in 1889 for the World's Fair in Paris. It stands 330 metres tall including antennas. Gustave Eiffel's engineering company built the structure in just over two years.",
  },
  {
    label: "Tech Markets 2024",
    mode: "text" as const,
    text: "Major semiconductor and technology stocks tumbled on August 5, 2024, amid macroeconomic concerns over economic growth and unwinding of the global currency carry trade.",
  },
];

export default function VerifyInput({
  onReportReady,
  onCancel,
  compact = false,
}: Props) {
  const [mode, setMode] = useState<"text" | "url">("text");
  const [inputVal, setInputVal] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Which evidence pool the backend should use. Mirrors the /api/verify `mode` field,
  // and is echoed back into the report label, so it is never a silent substitution.
  const [evidenceMode, setEvidenceMode] = useState<EvidenceMode>("auto");
  const [elapsed, setElapsed] = useState(0);
  const timer = useRef<number | null>(null);

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setErrorMsg(null);

    const trimmed = inputVal.trim();
    if (!trimmed) {
      setErrorMsg(mode === "url" ? "Paste an article URL first" : "Paste the text you want checked first");
      return;
    }
    if (mode === "url") {
      try {
        const parsed = new URL(trimmed.startsWith("http") ? trimmed : `https://${trimmed}`);
        if (!parsed.hostname.includes(".")) throw new Error("no dot in host");
      } catch {
        setErrorMsg("That is not a URL the extractor can try. Include the host, e.g. https://example.com/article");
        return;
      }
    }

    setLoading(true);
    setElapsed(0);
    const startedAt = performance.now();
    timer.current = window.setInterval(() => setElapsed((performance.now() - startedAt) / 1000), 100);
    try {
      // One call, real stages, real timings - no scripted progress animation.
      const { report } = await verifyInput(trimmed, mode, evidenceMode);
      onReportReady(report);
    } catch (err) {
      const message =
        err instanceof PipelineError
          ? err.message
          : `${(err as Error)?.name || "Error"}: ${(err as Error)?.message || "the pipeline did not answer"}`;
      setErrorMsg(message);
    } finally {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
      setLoading(false);
    }
  };

  const handlePreset = (preset: typeof PRESETS[0]) => {
    setMode(preset.mode);
    setInputVal(preset.text);
    setErrorMsg(null);
  };

  const handleClear = () => {
    setInputVal("");
    setErrorMsg(null);
  };

  return (
    <div
      className={`rounded-2xl border border-black/15 bg-white p-5 sm:p-7 shadow-lg transition-all ${
        compact ? "max-w-xl mx-auto" : "w-full"
      }`}
    >
      {/* Header and Mode Selector */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4 border-b border-black/10">
        <div className="flex items-center gap-2">
          <span className="inline-block w-2.5 h-2.5 rounded-full bg-black" />
          <h3
            className="text-[17px] sm:text-[19px] font-medium tracking-tight text-black"
            style={{ fontFamily: "var(--font-heading)" }}
          >
            Verify an Article or Claim
          </h3>
        </div>

        {/* Tab Toggle: Text vs URL */}
        <div className="inline-flex rounded-full border border-black/15 bg-black/[0.04] p-1">
          <button
            type="button"
            data-mode="text"
            onClick={() => {
              setMode("text");
              setErrorMsg(null);
            }}
            className={`rounded-full px-4 py-1 text-[12px] font-medium transition-all ${
              mode === "text"
                ? "bg-black text-white shadow-sm"
                : "text-black/60 hover:text-black"
            }`}
          >
            Paste Text
          </button>
          <button
            type="button"
            data-mode="url"
            onClick={() => {
              setMode("url");
              setErrorMsg(null);
            }}
            className={`rounded-full px-4 py-1 text-[12px] font-medium transition-all ${
              mode === "url"
                ? "bg-black text-white shadow-sm"
                : "text-black/60 hover:text-black"
            }`}
          >
            Enter URL
          </button>
        </div>
      </div>

      {/* Main Input Form */}
      <form onSubmit={handleSubmit} className="mt-4">
        {mode === "text" ? (
          <div>
            <label htmlFor="articleInput" className="sr-only">
              Article Text
            </label>
            <textarea
              id="articleInput"
              rows={4}
              value={inputVal}
              onChange={(e) => {
                setInputVal(e.target.value);
                if (errorMsg) setErrorMsg(null);
              }}
              placeholder="Paste article paragraphs, statements, or factual claims here to extract and verify with independent sources..."
              className="w-full rounded-xl border border-black/15 bg-[#fbfbfa] p-4 text-[14.5px] leading-relaxed text-black placeholder:text-black/35 focus:border-black focus:bg-white focus:outline-none transition-all resize-y"
              autoFocus
            />
          </div>
        ) : (
          <div>
            <label htmlFor="articleInput" className="sr-only">
              Article URL
            </label>
            <div className="relative">
              <span className="absolute left-4 top-1/2 -translate-y-1/2 text-black/40 text-[14px]">
                🔗
              </span>
              <input
                id="articleInput"
                type="url"
                value={inputVal}
                onChange={(e) => {
                  setInputVal(e.target.value);
                  if (errorMsg) setErrorMsg(null);
                }}
                placeholder="https://example.com/news/article-headline..."
                className="w-full rounded-xl border border-black/15 bg-[#fbfbfa] py-3.5 pl-11 pr-4 text-[14.5px] text-black placeholder:text-black/35 focus:border-black focus:bg-white focus:outline-none transition-all"
                autoFocus
              />
            </div>
          </div>
        )}

        {/* Inline Error Notice */}
        {errorMsg && (
          <div className="field-note mt-2.5 flex items-center gap-1.5 text-[12.5px] text-[#96301e] font-medium">
            <span>⚠</span>
            <span>{errorMsg}</span>
          </div>
        )}

        {/* Quick Example Presets */}
        <div className="mt-4 flex flex-wrap items-center gap-2 pt-2">
          <span className="mono text-[10px] text-black/45 uppercase tracking-wider mr-1">
            Try Example:
          </span>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => handlePreset(p)}
              className="rounded-full border border-black/10 bg-black/[0.03] px-3 py-1 text-[11.5px] text-black/70 hover:bg-black/10 hover:text-black transition-colors"
            >
              {p.label}
            </button>
          ))}
          {inputVal && (
            <button
              type="button"
              onClick={handleClear}
              className="ml-auto text-[11.5px] text-black/45 hover:text-black underline underline-offset-2 transition-colors"
            >
              Clear input
            </button>
          )}
        </div>

        {/* Evidence pool: what the backend is allowed to query */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="mono text-[10px] text-black/45 uppercase tracking-wider mr-1">Evidence:</span>
          {(["auto", "live", "offline", "snapshot"] as EvidenceMode[]).map((key) => (
            <button
              key={key}
              type="button"
              data-evidence={key}
              onClick={() => setEvidenceMode(key)}
              className={`rounded-full border px-3 py-1 text-[11.5px] transition-colors ${
                evidenceMode === key
                  ? "border-black bg-black text-white"
                  : "border-black/10 bg-black/[0.03] text-black/65 hover:bg-black/10 hover:text-black"
              }`}
            >
              {key}
            </button>
          ))}
          <span className="text-[11px] text-black/40">
            {evidenceMode === "offline"
              ? "local demo corpus only - results are labelled as a mechanism demo"
              : evidenceMode === "snapshot"
              ? "replays a captured retrieval - labelled as a demo backup"
              : evidenceMode === "live"
              ? "queries the real source adapters"
              : "live if the network and keys allow it, otherwise the local corpus"}
          </span>
        </div>

        {/* Action Controls */}
        <div className="mt-5 flex items-center justify-between gap-3 border-t border-black/10 pt-4">
          <p className="text-[11.5px] text-black/45 hidden sm:block">
            Press <kbd className="font-mono bg-black/[0.06] px-1.5 py-0.5 rounded text-[10px]">Enter</kbd> to verify
          </p>

          <div className="flex items-center gap-2 ml-auto">
            {onCancel && (
              <button
                type="button"
                onClick={onCancel}
                className="pill border border-black/15 px-4 py-2 text-[13px] text-black/70 hover:text-black hover:border-black/30 transition-colors"
              >
                Cancel
              </button>
            )}

            <button
              id="runBtn"
              type="submit"
              disabled={loading}
              className="pill bg-black text-white px-6 py-2.5 text-[13.5px] font-medium hover:bg-black/85 transition-all flex items-center gap-2 shadow-sm disabled:opacity-70"
            >
              {loading ? (
                <>
                  <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  <span>Extracting, retrieving, scoring &middot; {elapsed.toFixed(1)}s</span>
                </>
              ) : (
                <>
                  <span>Verify Article</span>
                  <span>→</span>
                </>
              )}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

