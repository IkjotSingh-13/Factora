import React from "react";

interface Props {
  onClose: () => void;
  onOpenReport: () => void;
}

const COMPONENTS: [string, string, string, string][] = [
  [
    "C",
    "Source Credibility",
    "0.30",
    "Primary archives, peer-reviewed journals, and regulatory registries receive the highest weights.",
  ],
  [
    "R",
    "Evidence Relevance",
    "0.30",
    "Direct propositional match between retrieved passages and the claim statement.",
  ],
  [
    "A",
    "Independent Agreement",
    "0.20",
    "Number of distinct publisher clusters confirming the finding without wire syndication.",
  ],
  [
    "T",
    "Freshness",
    "0.10",
    "Recency of corroborating records for time-sensitive factual assertions.",
  ],
  [
    "K",
    "Context Consistency",
    "0.10",
    "Penalizes statements extracted out of context or qualified in the source article.",
  ],
];

export default function MethodSheet({ onClose, onOpenReport }: Props) {
  return (
    <div className="relative z-[2] min-h-screen bg-[var(--bg)] paper">
      <div className="mx-auto max-w-[760px] px-5 sm:px-8 pt-[84px] pb-16 sm:pt-[96px] sm:pb-20">
        {/* Navigation Breadcrumb */}
        <div className="flex items-center gap-2 mb-4 text-[13px]">
          <a
            href="#home"
            onClick={(e) => {
              e.preventDefault();
              onClose();
            }}
            className="text-black/60 hover:text-black font-medium transition-colors"
          >
            ← Back to Home
          </a>
          <span className="text-black/25">/</span>
          <span className="font-mono text-[11px] text-black/50 uppercase tracking-wider">
            Methodology
          </span>
        </div>

        {/* Page Title */}
        <h1
          className="text-[34px] sm:text-[44px] leading-tight tracking-tight text-black"
          style={{ fontFamily: "var(--font-heading)" }}
        >
          Scoring Methodology
        </h1>

        <p className="mt-2 text-[15px] sm:text-[16px] text-black/65 leading-relaxed">
          Evidence strength and stance direction are computed independently. A claim can have strong evidence that refutes it.
        </p>

        {/* Formula Card */}
        <div className="mt-6 rounded-2xl border border-black/10 bg-white p-6 shadow-sm">
          <div className="font-mono text-[11px] text-[#FF5A1F] uppercase font-semibold tracking-wider">
            Composite Evidence Formula
          </div>
          <div
            className="mt-2 text-[20px] sm:text-[24px] font-mono tracking-tight text-black font-medium"
            style={{ fontFamily: "var(--font-heading)" }}
          >
            S = 0.30·C + 0.30·R + 0.20·A + 0.10·T + 0.10·K
          </div>
          <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] font-mono text-black/50">
            <span>C: Credibility</span>
            <span>·</span>
            <span>R: Relevance</span>
            <span>·</span>
            <span>A: Agreement</span>
            <span>·</span>
            <span>T: Freshness</span>
            <span>·</span>
            <span>K: Context</span>
          </div>
        </div>

        {/* Five Weighted Components */}
        <div className="mt-8 divide-y divide-black/10 border-t border-b border-black/10">
          {COMPONENTS.map(([code, name, weight, detail]) => (
            <div key={code} className="py-4 grid grid-cols-[40px_1fr] gap-4 items-start">
              <div className="pt-0.5">
                <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-black text-white text-[11px] font-mono font-medium">
                  {code}
                </span>
                <span className="font-mono text-[10px] text-black/45 block mt-1 text-center">
                  {weight}
                </span>
              </div>
              <div>
                <h2 className="text-[15px] font-medium text-black">{name}</h2>
                <p className="mt-0.5 text-[13px] leading-relaxed text-black/60">
                  {detail}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* Two Core Guardrails */}
        <div className="mt-8 grid sm:grid-cols-2 gap-4">
          <div className="rounded-xl border border-black/10 bg-white p-5 shadow-sm">
            <h3 className="text-[15px] font-medium text-black">
              Decoupled Direction
            </h3>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-black/60">
              Strength measures evidence volume; stance (Supports / Refutes) measures direction. Strongly refuted claims remain high-confidence.
            </p>
          </div>

          <div className="rounded-xl border border-black/10 bg-white p-5 shadow-sm">
            <h3 className="text-[15px] font-medium text-black">
              Verbatim Quote Guard
            </h3>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-black/60">
              Quotes are matched directly to source character bytes. Non-matching quotes are permanently dropped from the evidence score.
            </p>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="mt-10 flex flex-wrap items-center gap-3 pt-5 border-t border-black/10">
          <button
            onClick={onOpenReport}
            className="rounded-full bg-[#FF5A1F] text-white px-5 py-2 text-[12.5px] font-medium hover:bg-[#e04810] transition-colors shadow-sm"
          >
            View Live Report
          </button>
          <a
            href="#limits"
            className="rounded-full border border-black/20 bg-white/70 px-4 py-2 text-[12.5px] font-medium text-black hover:bg-white transition-colors"
          >
            System Boundaries →
          </a>
          <button
            onClick={onClose}
            className="text-[12.5px] text-black/50 hover:text-black transition-colors ml-auto"
          >
            Return to Home
          </button>
        </div>
      </div>
    </div>
  );
}
