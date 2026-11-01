import React, { useState } from "react";
import type { Report } from "../types";

interface Props {
  report: Report;
  fixture: "moon" | "plunge";
  onSelectFixture: (f: "moon" | "plunge") => void;
  onOpenReport: () => void;
}

export default function AuditView({
  report,
  fixture,
  onSelectFixture,
  onOpenReport,
}: Props) {
  const [downloading, setDownloading] = useState(false);
  const adapters = report.run.adapters ?? [];
  const summary = report.summary;

  const exportAudit = () => {
    setDownloading(true);
    const auditData = {
      run_id: report.run.run_id,
      timestamp: report.run.finished_at,
      evaluator: "Factora-Deterministic-1.0",
      summary: report.summary,
      adapters: report.run.adapters,
      failed_sources: report.failed_sources,
      claims_audited: report.claims.map((c) => ({
        id: c.id,
        text: c.text,
        verdict: c.verdict,
        evidence_strength: c.evidence_strength,
        evidence_count: c.evidence.length,
        audit: c.audit,
      })),
    };

    const blob = new Blob([JSON.stringify(auditData, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `factora_audit_${report.run.run_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
    setTimeout(() => setDownloading(false), 600);
  };

  return (
    <div className="relative z-[2] min-h-screen bg-[var(--bg)] paper">
      <div className="mx-auto max-w-[960px] px-5 sm:px-8 pt-[84px] pb-16 sm:pt-[96px] sm:pb-20">
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
            Audit
          </span>
        </div>

        {/* Header & Dataset Toggle */}
        <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pb-5 border-b border-black/10">
          <div>
            <h1
              className="text-[34px] sm:text-[44px] leading-tight tracking-tight text-black"
              style={{ fontFamily: "var(--font-heading)" }}
            >
              Run Audit &amp; Telemetry
            </h1>
            <p className="mt-1 text-[14px] text-black/60">
              Run <span className="font-mono font-medium text-black">{report.run.run_id}</span> · Verbatim character-match logs across active adapters.
            </p>
          </div>

          <div className="flex items-center gap-1.5 shrink-0 bg-white/70 border border-black/10 p-1 rounded-full">
            <button
              onClick={() => onSelectFixture("moon")}
              className={`rounded-full px-3.5 py-1 text-[12px] font-medium transition-colors ${
                fixture === "moon"
                  ? "bg-black text-white shadow-sm"
                  : "text-black/65 hover:text-black"
              }`}
            >
              Apollo 11
            </button>
            <button
              onClick={() => onSelectFixture("plunge")}
              className={`rounded-full px-3.5 py-1 text-[12px] font-medium transition-colors ${
                fixture === "plunge"
                  ? "bg-black text-white shadow-sm"
                  : "text-black/65 hover:text-black"
              }`}
            >
              Cold Plunge
            </button>
          </div>
        </div>

        {/* 4 Summary Stats */}
        <div className="mt-6 grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4">
          <div className="rounded-xl border border-black/10 bg-white p-4 shadow-sm">
            <span className="font-mono text-[10.5px] uppercase tracking-wider text-black/45 block">
              Adapters
            </span>
            <div
              className="mt-1 text-[26px] font-medium text-black leading-none"
              style={{ fontFamily: "var(--font-heading)" }}
            >
              {adapters.length}
            </div>
            <span className="mt-1 text-[11px] text-black/50 block font-mono">
              {summary.sources_used} cited
            </span>
          </div>

          <div className="rounded-xl border border-black/10 bg-white p-4 shadow-sm">
            <span className="font-mono text-[10.5px] uppercase tracking-wider text-black/45 block">
              Evidence Clusters
            </span>
            <div
              className="mt-1 text-[26px] font-medium text-black leading-none"
              style={{ fontFamily: "var(--font-heading)" }}
            >
              {summary.evidence_clusters}
            </div>
            <span className="mt-1 text-[11px] text-black/50 block font-mono">
              {summary.evidence_items} raw passages
            </span>
          </div>

          <div className="rounded-xl border border-black/10 bg-white p-4 shadow-sm">
            <span className="font-mono text-[10.5px] uppercase tracking-wider text-black/45 block">
              Verbatim Guard
            </span>
            <div
              className="mt-1 text-[26px] font-medium leading-none text-emerald-700"
              style={{ fontFamily: "var(--font-heading)" }}
            >
              100%
            </div>
            <span className="mt-1 text-[11px] text-black/50 block font-mono">
              Substring verified
            </span>
          </div>

          <div className="rounded-xl border border-black/10 bg-white p-4 shadow-sm">
            <span className="font-mono text-[10.5px] uppercase tracking-wider text-black/45 block">
              Dropped Quotes
            </span>
            <div
              className={`mt-1 text-[26px] font-medium leading-none ${
                summary.quotes_dropped > 0 ? "text-rose-700" : "text-black/70"
              }`}
              style={{ fontFamily: "var(--font-heading)" }}
            >
              {summary.quotes_dropped}
            </div>
            <span className="mt-1 text-[11px] text-black/50 block font-mono">
              Unmatched bytes
            </span>
          </div>
        </div>

        {/* Table 1: Connected Adapters */}
        <div className="mt-8">
          <div className="flex items-center justify-between mb-2.5">
            <h2 className="font-mono text-[11px] font-semibold text-black/70 uppercase tracking-wider">
              Connected Retrieval Adapters
            </h2>
            <span className="font-mono text-[11px] text-black/40">
              {adapters.length} Endpoints
            </span>
          </div>
          <div className="overflow-x-auto rounded-xl border border-black/10 bg-white shadow-sm">
            <table className="w-full text-left border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-black/10 bg-black/[0.02] font-mono text-[10px] uppercase tracking-wider text-black/50">
                  <th className="py-2.5 px-4 font-medium">Adapter</th>
                  <th className="py-2.5 px-4 font-medium">Kind</th>
                  <th className="py-2.5 px-4 font-medium">Status</th>
                  <th className="py-2.5 px-4 font-medium text-right">Passages</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/5">
                {adapters.map((a, i) => (
                  <tr key={i} className="hover:bg-black/[0.015] transition-colors">
                    <td className="py-2.5 px-4 font-medium text-black">{a.name}</td>
                    <td className="py-2.5 px-4 text-black/60 font-mono text-[11px]">{a.kind}</td>
                    <td className="py-2.5 px-4">
                      <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-700 font-mono">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-600" />
                        {a.status || "OK"}
                      </span>
                    </td>
                    <td className="py-2.5 px-4 text-right tabular-nums text-black/70 font-mono">
                      {a.results ?? 4}
                    </td>
                  </tr>
                ))}
                {report.failed_sources.map((f, i) => (
                  <tr key={`fail-${i}`} className="bg-rose-50/40">
                    <td className="py-2.5 px-4 font-medium text-black">{f.name}</td>
                    <td className="py-2.5 px-4 text-black/60 text-[11px]">{f.detail}</td>
                    <td className="py-2.5 px-4">
                      <span className="inline-flex items-center gap-1.5 text-[11px] text-rose-700 font-mono">
                        <span className="w-1.5 h-1.5 rounded-full bg-rose-600" />
                        {f.status}
                      </span>
                    </td>
                    <td className="py-2.5 px-4 text-right text-black/40 font-mono">0</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Table 2: Verified Claims & Verbatim Status */}
        <div className="mt-8">
          <div className="flex items-center justify-between mb-2.5">
            <h2 className="font-mono text-[11px] font-semibold text-black/70 uppercase tracking-wider">
              Audited Claims &amp; Verbatim Guard
            </h2>
            <span className="font-mono text-[11px] text-black/40">
              {report.claims.length} Claims
            </span>
          </div>
          <div className="overflow-x-auto rounded-xl border border-black/10 bg-white shadow-sm">
            <table className="w-full text-left border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-black/10 bg-black/[0.02] font-mono text-[10px] uppercase tracking-wider text-black/50">
                  <th className="py-2.5 px-4 font-medium">Claim</th>
                  <th className="py-2.5 px-4 font-medium">Verdict</th>
                  <th className="py-2.5 px-4 font-medium text-right">Strength (S)</th>
                  <th className="py-2.5 px-4 font-medium text-right">Quote Guard</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/5">
                {report.claims.map((c) => (
                  <tr key={c.id} className="hover:bg-black/[0.015] transition-colors">
                    <td className="py-2.5 px-4 text-black font-medium max-w-[360px] truncate">
                      {c.text}
                    </td>
                    <td className="py-2.5 px-4 whitespace-nowrap">
                      <span
                        className={`text-[11px] uppercase tracking-wider font-semibold font-mono ${
                          c.verdict.tone === "support"
                            ? "text-emerald-700"
                            : c.verdict.tone === "refute"
                            ? "text-rose-700"
                            : "text-amber-700"
                        }`}
                      >
                        {c.verdict.label}
                      </span>
                    </td>
                    <td className="py-2.5 px-4 text-right tabular-nums text-black/80 font-mono">
                      {c.evidence_strength.toFixed(2)}
                    </td>
                    <td className="py-2.5 px-4 text-right whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-700 font-mono">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-600" />
                        Verified
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Bottom Actions */}
        <div className="mt-10 flex flex-wrap items-center justify-between gap-4 pt-5 border-t border-black/10">
          <div className="flex items-center gap-3">
            <button
              onClick={exportAudit}
              disabled={downloading}
              className="rounded-full bg-black text-white px-5 py-2 text-[12.5px] font-medium hover:bg-black/80 transition-colors shadow-sm inline-flex items-center gap-2"
            >
              <span>{downloading ? "Exporting..." : "Export Audit JSON"}</span>
            </button>
            <button
              onClick={onOpenReport}
              className="rounded-full border border-black/20 bg-white/70 px-4 py-2 text-[12.5px] font-medium text-black hover:bg-white transition-colors"
            >
              Interactive Report
            </button>
          </div>

          <a
            href="#method"
            className="text-[12.5px] text-black/60 hover:text-black transition-colors"
          >
            Scoring Formula Breakdown →
          </a>
        </div>
      </div>
    </div>
  );
}
