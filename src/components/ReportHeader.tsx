import React from "react";
import type { Report } from "../types";
import { stamp } from "../lib";

export default function ReportHeader({
  report,
  onBack,
}: {
  report: Report;
  onBack: () => void;
}) {
  const s = report.summary;
  const supportedCount = s.status_counts.supported ?? 0;
  const refutedCount = s.status_counts.refuted ?? 0;
  const contestedCount = s.status_counts.contested ?? 0;
  const total = s.claims_checked || 1;

  const isPredominantlySupported = supportedCount >= total * 0.5;
  const isPredominantlyRefuted = refutedCount >= total * 0.5;

  return (
    <div className="rounded-2xl border border-black/10 bg-white p-6 sm:p-8 shadow-sm">
      {/* Top Meta Line */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-[12px] text-black/50 pb-4 border-b border-black/10">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-[#1f5740]" />
          <span className="font-medium text-black/70">Verified Evidence Audit</span>
          <span>·</span>
          <span>{report.article.origin}</span>
          <span>·</span>
          <span>{stamp(report.run.finished_at)}</span>
        </div>

        <div className="font-mono text-black/40">
          Run ID: <span className="font-semibold text-black/70">{report.run.run_id}</span>
        </div>
      </div>

      {/* Main Title & Executive Stance Banner */}
      <div className="mt-5 grid md:grid-cols-[1fr_auto] gap-6 items-start">
        <div>
          <span className="font-mono text-[11px] uppercase tracking-wider text-black/40">
            Audited Article
          </span>
          <h1
            className="mt-1 text-[26px] sm:text-[34px] font-medium tracking-tight text-black leading-snug"
            style={{ fontFamily: "var(--font-heading)" }}
          >
            {report.article.title}
          </h1>
          <p className="mt-2 text-[13px] text-black/60 max-w-2xl leading-relaxed">
            {report.article.excerpt}
          </p>
        </div>

        {/* Verdict Badge */}
        <div className="flex flex-col items-start sm:items-end gap-2 shrink-0">
          <span
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-[13px] font-semibold tracking-wide ${
              isPredominantlySupported
                ? "bg-[#ecfdf5] text-[#065f46] border border-[#a7f3d0]"
                : isPredominantlyRefuted
                ? "bg-[#fef2f2] text-[#991b1b] border border-[#fecaca]"
                : "bg-[#fffbeb] text-[#92400e] border border-[#fde68a]"
            }`}
          >
            <span className="w-2 h-2 rounded-full currentColor animate-pulse" />
            <span>
              {isPredominantlySupported
                ? "Corroborated by Records"
                : isPredominantlyRefuted
                ? "Contradicted by Facts"
                : "Mixed Evidence Findings"}
            </span>
          </span>

          <div className="flex items-center gap-2 mt-1">
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 text-emerald-800 text-[11.5px] font-medium border border-emerald-200">
              ✓ {supportedCount} Supported
            </span>
            {refutedCount > 0 && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-rose-50 text-rose-800 text-[11.5px] font-medium border border-rose-200">
                ✗ {refutedCount} Refuted
              </span>
            )}
            {contestedCount > 0 && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-50 text-amber-800 text-[11.5px] font-medium border border-amber-200">
                • {contestedCount} Contested
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
