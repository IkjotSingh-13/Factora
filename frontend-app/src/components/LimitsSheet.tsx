import React from "react";

interface Props {
  onOpenReport: () => void;
  onOpenMethod: () => void;
}

const LIMITS = [
  {
    code: "01",
    title: "Breaking News & Rapidly Evolving Events",
    detail:
      "Events under 6 hours old often lack distinct multi-source corroboration. Wire syndications are flagged and deduplicated to avoid false confirmation.",
    tag: "Time Sensitivity",
  },
  {
    code: "02",
    title: "Idioms, Rhetoric & Metaphor",
    detail:
      "Literal NLP extraction can misinterpret figurative language. The Context Consistency score (K) heavily discounts claims where tone diverges from article meaning.",
    tag: "NLP Parsing",
  },
  {
    code: "03",
    title: "Paywalls & Private Databases",
    detail:
      "Verification is restricted to open-access registries, encyclopedias, and authorized public endpoints. Proprietary corporate databases cannot be queried.",
    tag: "Coverage",
  },
  {
    code: "04",
    title: "Refusal on Thin Evidence",
    detail:
      "If evidence strength falls below S < 0.35 or sources directly conflict without resolution, Factora returns Insufficient Evidence rather than an ungrounded guess.",
    tag: "Guardrail",
  },
  {
    code: "05",
    title: "Deterministic Scores vs. Absolute Truth",
    detail:
      "The S score measures evidence volume, relevance, diversity, and freshness. It represents verifiable documentation, not an infallible oracle.",
    tag: "Epistemic Bound",
  },
];

export default function LimitsSheet({ onOpenReport, onOpenMethod }: Props) {
  return (
    <div className="relative z-[2] min-h-screen bg-[var(--bg)] paper">
      <div className="mx-auto max-w-[760px] px-5 sm:px-8 pt-[84px] pb-16 sm:pt-[96px] sm:pb-20">
        {/* Navigation Breadcrumb */}
        <div className="flex items-center gap-2 mb-4 text-[13px]">
          <a
            href="#home"
            className="text-black/60 hover:text-black font-medium transition-colors"
          >
            ← Back to Home
          </a>
          <span className="text-black/25">/</span>
          <span className="font-mono text-[11px] text-black/50 uppercase tracking-wider">
            Boundaries
          </span>
        </div>

        {/* Headline */}
        <h1
          className="text-[34px] sm:text-[44px] leading-tight tracking-tight text-black"
          style={{ fontFamily: "var(--font-heading)" }}
        >
          System Boundaries &amp; Limits
        </h1>

        <p className="mt-2 text-[15px] sm:text-[16px] text-black/65 leading-relaxed">
          Factora verifies documented evidence across public registries—never an unsupported AI verdict.
        </p>

        {/* Principle Callout */}
        <div className="mt-6 rounded-2xl border border-black/10 bg-white p-5 shadow-sm">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-[#FF5A1F]" />
            <h3 className="text-[13.5px] font-semibold text-black uppercase tracking-wider font-mono">
              Core Epistemic Rule
            </h3>
          </div>
          <p className="mt-1.5 text-[13.5px] text-black/70 leading-relaxed">
            When evidence is thin, conflicting, or unverifiable, Factora refuses to speculate. An <strong>Insufficient Evidence</strong> result is a valid and transparent outcome.
          </p>
        </div>

        {/* 5 Boundaries List */}
        <div className="mt-8 divide-y divide-black/10 border-t border-b border-black/10">
          {LIMITS.map((item) => (
            <div key={item.code} className="py-4 grid md:grid-cols-[120px_1fr] gap-3 items-start">
              <div>
                <span className="font-mono text-black/40 text-[10.5px] block font-semibold">
                  {item.code}
                </span>
                <span className="inline-block mt-0.5 text-[10px] uppercase font-mono tracking-wider text-black/60 bg-black/[0.05] rounded px-1.5 py-0.5">
                  {item.tag}
                </span>
              </div>
              <div>
                <h2 className="text-[15.5px] font-medium text-black tracking-tight">
                  {item.title}
                </h2>
                <p className="mt-1 text-[13px] leading-relaxed text-black/60">
                  {item.detail}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* Navigation Actions */}
        <div className="mt-10 flex flex-wrap items-center gap-3 pt-5 border-t border-black/10">
          <button
            onClick={onOpenMethod}
            className="rounded-full bg-[#FF5A1F] text-white px-5 py-2 text-[12.5px] font-medium hover:bg-[#e04810] transition-colors shadow-sm"
          >
            Review Scoring Method
          </button>
          <button
            onClick={onOpenReport}
            className="rounded-full border border-black/20 bg-white/70 px-4 py-2 text-[12.5px] font-medium text-black hover:bg-white transition-colors"
          >
            Live Evidence Report
          </button>
          <a
            href="#audit"
            className="text-[12.5px] text-black/50 hover:text-black transition-colors ml-auto"
          >
            Audit Log Manifest →
          </a>
        </div>
      </div>
    </div>
  );
}
