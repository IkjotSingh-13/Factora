import React, { useState } from "react";
import type { Claim, Report } from "../types";
import { fixed, shortDate } from "../lib";

export default function ClaimDetail({
  report,
  claim,
  onCopy,
  onSource,
}: {
  report: Report;
  claim: Claim;
  onCopy: (id: string) => void;
  onSource: (payload: {
    title: string;
    domain: string;
    kind: string;
    date: string;
    stance: string;
    quote: string;
    url: string;
  }) => void;
}) {
  const [showFormula, setShowFormula] = useState(false);

  const isSupported = claim.verdict.tone === "support";
  const isRefuted = claim.verdict.tone === "refute";
  const strength = claim.evidence_strength;

  return (
    <div className="space-y-5">
      {/* 1. Main Claim & Verdict Banner */}
      <div className="rounded-2xl border border-black/10 bg-white p-6 shadow-sm">
        {/* Top Badges */}
        <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-black/10">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider ${
                isSupported
                  ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
                  : isRefuted
                  ? "bg-rose-50 text-rose-800 border border-rose-200"
                  : "bg-amber-50 text-amber-800 border border-amber-200"
              }`}
            >
              <span>{isSupported ? "✓" : isRefuted ? "✗" : "•"}</span>
              <span>{claim.verdict.label}</span>
            </span>

            <span className="font-mono text-[11px] text-black/50">
              Evidence Score: <strong className="text-black font-semibold">S = {fixed(strength, 2)}</strong> / 1.00
            </span>
          </div>

          <span className="font-mono text-[11px] text-black/40">
            {claim.evidence.length} Cited Source{claim.evidence.length !== 1 ? "s" : ""}
          </span>
        </div>

        {/* Claim Headline */}
        <h2
          className="mt-4 text-[22px] sm:text-[26px] font-medium leading-snug tracking-tight text-black"
          style={{ fontFamily: "var(--font-heading)" }}
        >
          {claim.text}
        </h2>

        {/* Verbatim Article Excerpt */}
        <div className="mt-4 rounded-xl border border-black/10 bg-[#fbfbfa] p-4">
          <div className="font-mono text-[10.5px] uppercase tracking-wider text-black/40 mb-1">
            Article Quote:
          </div>
          <blockquote className="text-[13.5px] leading-relaxed text-black/80 italic border-l-2 border-black/20 pl-3">
            &ldquo;{claim.article_quote}&rdquo;
          </blockquote>
        </div>

        {/* Verdict Summary & Quick Score Components */}
        <div className="mt-4 pt-4 border-t border-black/10 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <p className="text-[13px] text-black/70 leading-relaxed max-w-xl">
            {claim.verdict.one_line}
          </p>

          <button
            onClick={() => setShowFormula((prev) => !prev)}
            className="text-[11.5px] font-mono text-[#FF5A1F] hover:underline underline-offset-4 shrink-0"
          >
            {showFormula ? "Hide Formula" : "View S Equation ↓"}
          </button>
        </div>

        {/* Expandable Equation Panel */}
        {showFormula && (
          <div className="mt-3 p-4 rounded-xl bg-black/[0.03] border border-black/10 text-[12px] font-mono space-y-2">
            <div className="font-semibold text-black/80">
              Deterministic Formulation: S = 0.30C + 0.30R + 0.20A + 0.10T + 0.10K
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 pt-2 border-t border-black/10 text-black/60 text-[11px]">
              <div>Credibility (C): <strong>{fixed(claim.scores.c.value, 2)}</strong></div>
              <div>Relevance (R): <strong>{fixed(claim.scores.r.value, 2)}</strong></div>
              <div>Agreement (A): <strong>{fixed(claim.scores.a.value, 2)}</strong></div>
              <div>Freshness (T): <strong>{fixed(claim.scores.t.value, 2)}</strong></div>
              <div>Context (K): <strong>{fixed(claim.scores.k.value, 2)}</strong></div>
            </div>
          </div>
        )}
      </div>

      {/* 2. Insufficient Evidence Notice (if applicable) */}
      {claim.insufficient.is && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50/80 p-5 shadow-sm">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-600" />
            <h3 className="text-[14px] font-semibold text-amber-900">
              Insufficient Evidence for Conclusive Verdict
            </h3>
          </div>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-amber-800">
            {claim.insufficient.reasons.join(". ")}
          </p>
        </div>
      )}

      {/* 3. Documented Evidence Sources */}
      <div className="rounded-2xl border border-black/10 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between pb-3 border-b border-black/10">
          <h3
            className="text-[18px] font-medium text-black"
            style={{ fontFamily: "var(--font-heading)" }}
          >
            Corroborating Evidence Passages
          </h3>
          <span className="font-mono text-[11px] text-black/40">
            {claim.evidence.length} Verified Records
          </span>
        </div>

        <div className="mt-4 space-y-3">
          {claim.evidence.map((ev, i) => {
            const isEvSupport = ev.stance === "SUPPORTS";
            const isEvRefute = ev.stance === "REFUTES";

            const payload = {
              title: ev.title,
              domain: ev.domain,
              kind: ev.source_kind,
              date: ev.published_at ? shortDate(ev.published_at) : "Historical record",
              stance: ev.stance,
              quote: ev.analysis.quote || "",
              url: ev.url,
            };

            return (
              <article
                key={`${ev.domain}-${i}`}
                className="rounded-xl border border-black/10 bg-[#fbfbfa] p-4 transition-all hover:border-black/25"
              >
                {/* Source Meta Header */}
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-black/75 bg-black/[0.05] px-2 py-0.5 rounded">
                      {ev.domain}
                    </span>
                    <span className="text-[11px] text-black/45">
                      {ev.source_kind} · {ev.published_at ? shortDate(ev.published_at) : "Primary Archive"}
                    </span>
                  </div>

                  <span
                    className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider ${
                      isEvSupport
                        ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
                        : isEvRefute
                        ? "bg-rose-50 text-rose-800 border border-rose-200"
                        : "bg-amber-50 text-amber-800 border border-amber-200"
                    }`}
                  >
                    {ev.stance}
                  </span>
                </div>

                {/* Document Title / Link */}
                <button
                  onClick={() => onSource(payload)}
                  className="text-left text-[14.5px] font-medium leading-snug text-black hover:text-[#FF5A1F] transition-colors block"
                >
                  {ev.title} ↗
                </button>

                {/* Verbatim Quote */}
                {ev.analysis.quote && (
                  <div className="mt-2.5 p-3 rounded-lg bg-white border border-black/10">
                    <div className="flex items-center gap-1.5 text-[10px] font-mono text-[#1f5740] font-semibold mb-1">
                      <span>✓ Verbatim Substring Match</span>
                      <span className="text-black/30">·</span>
                      <span className="text-black/40">Relevance: {Math.round(ev.relevance * 100)}%</span>
                    </div>
                    <blockquote className="text-[13px] leading-relaxed text-black/85 italic">
                      &ldquo;{ev.analysis.quote}&rdquo;
                    </blockquote>
                  </div>
                )}

                {/* Analysis / Rationale */}
                {ev.analysis.rationale && (
                  <p className="mt-2 text-[12px] leading-relaxed text-black/60">
                    <strong className="text-black/80 font-medium">Evidence synthesis: </strong>
                    {ev.analysis.rationale}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
