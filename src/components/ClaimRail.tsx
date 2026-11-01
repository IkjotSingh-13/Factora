import React from "react";
import type { Claim, Report } from "../types";

export default function ClaimRail({
  report,
  activeId,
  onSelect,
}: {
  report: Report;
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <aside className="w-full">
      <div className="flex items-center justify-between mb-3 px-1">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-black/60 font-mono">
          Extracted Claims ({report.claims.length})
        </h2>
        <span className="text-[11px] text-black/40">Select to inspect</span>
      </div>

      <div className="flex flex-col gap-2">
        {report.claims.map((claim, index) => {
          const active = claim.id === activeId;
          const isSupported = claim.verdict.tone === "support";
          const isRefuted = claim.verdict.tone === "refute";

          return (
            <button
              key={claim.id}
              onClick={() => onSelect(claim.id)}
              className={`text-left w-full rounded-xl p-3.5 transition-all duration-150 border ${
                active
                  ? "bg-white border-black/40 shadow-sm ring-1 ring-black/10"
                  : "bg-white/60 hover:bg-white border-black/10 hover:border-black/20"
              }`}
              aria-current={active ? "true" : undefined}
            >
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <div className="flex items-center gap-2">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      isSupported
                        ? "bg-[#1f5740]"
                        : isRefuted
                        ? "bg-[#96301e]"
                        : "bg-[#75560f]"
                    }`}
                  />
                  <span className="font-mono text-[10.5px] uppercase font-semibold text-black/50">
                    Claim {index + 1}
                  </span>
                </div>

                <span
                  className={`text-[10.5px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider ${
                    isSupported
                      ? "bg-emerald-50 text-emerald-800"
                      : isRefuted
                      ? "bg-rose-50 text-rose-800"
                      : "bg-amber-50 text-amber-800"
                  }`}
                >
                  {claim.verdict.label}
                </span>
              </div>

              <div
                className={`text-[13px] leading-snug line-clamp-2 ${
                  active ? "text-black font-medium" : "text-black/75"
                }`}
              >
                {claim.text}
              </div>

              <div className="mt-2 flex items-center justify-between text-[11px] text-black/45 font-mono pt-1.5 border-t border-black/5">
                <span>{claim.evidence.length} sources</span>
                <span className="font-semibold text-black/70">
                  S = {claim.evidence_strength.toFixed(2)}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

export type { Claim };
