import { useEffect, useState } from "react";
import type { Report } from "../types";
import ReportHeader from "./ReportHeader";
import ClaimRail from "./ClaimRail";
import ClaimDetail from "./ClaimDetail";
import VerifyInput from "./VerifyInput";

export default function ReportView({
  report,
  fixture = "moon",
  onSelectFixture,
  onCustomReport,
  onBack,
}: {
  report: Report;
  fixture?: "moon" | "plunge" | "custom";
  onSelectFixture?: (f: "moon" | "plunge") => void;
  onCustomReport?: (report: Report) => void;
  onBack: () => void;
}) {
  const [activeId, setActiveId] = useState(
    report.default_claim_id || report.claims[0]?.id || "cl-1"
  );
  const [showInputModal, setShowInputModal] = useState(false);
  const [source, setSource] = useState<null | {
    title: string;
    domain: string;
    kind: string;
    date: string;
    stance: string;
    quote: string;
    url: string;
  }>(null);

  // Keep active claim valid if fixture changes
  useEffect(() => {
    if (!report.claims.some((c) => c.id === activeId)) {
      setActiveId(report.default_claim_id || report.claims[0]?.id || "cl-1");
    }
  }, [report, activeId]);

  const claim = report.claims.find((c) => c.id === activeId) ?? report.claims[0];

  useEffect(() => {
    document.body.style.overflow = "auto";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSource(null);
        setShowInputModal(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(report, null, 2)], {
      type: "application/json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `factora_report_${report.run.run_id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="relative z-[2] min-h-screen bg-[var(--bg)] paper">
      <div className="mx-auto max-w-[1200px] px-4 sm:px-6 md:px-8 pt-[84px] pb-16 sm:pt-[96px] sm:pb-20">
        {/* Top Control Bar: Home Link, Fixture Selector, +Verify Action */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-2 text-[13px]">
            <a
              href="#home"
              onClick={(e) => {
                e.preventDefault();
                onBack();
              }}
              className="text-black/60 hover:text-black font-medium transition-colors"
            >
              ← Back to Home
            </a>
            <span className="text-black/25">/</span>
            <span className="font-mono text-[11px] text-black/50 uppercase tracking-wider">
              Evidence Audit
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {onSelectFixture && (
              <div className="inline-flex rounded-full border border-black/15 bg-white/70 p-1 shadow-sm">
                <button
                  onClick={() => onSelectFixture("moon")}
                  className={`rounded-full px-3 py-1 text-[11.5px] font-medium transition-colors ${
                    fixture === "moon"
                      ? "bg-black text-white shadow-sm"
                      : "text-black/70 hover:text-black"
                  }`}
                >
                  Apollo 11
                </button>
                <button
                  onClick={() => onSelectFixture("plunge")}
                  className={`rounded-full px-3 py-1 text-[11.5px] font-medium transition-colors ${
                    fixture === "plunge"
                      ? "bg-black text-white shadow-sm"
                      : "text-black/70 hover:text-black"
                  }`}
                >
                  Cold Plunge
                </button>
                {fixture === "custom" && (
                  <span className="rounded-full bg-emerald-600 text-white px-3 py-1 text-[11.5px] font-medium shadow-sm">
                    Custom Text
                  </span>
                )}
              </div>
            )}

            <button
              onClick={() => setShowInputModal(true)}
              className="rounded-full bg-[#FF5A1F] text-white px-4 py-1.5 text-[12.5px] font-medium hover:bg-[#e04810] transition-all flex items-center gap-1.5 shadow-sm"
            >
              <span>+ Verify New Article</span>
            </button>

            <button
              onClick={exportJson}
              className="rounded-full border border-black/20 bg-white/70 hover:bg-white px-3.5 py-1.5 text-[12px] font-medium text-black/75 transition-colors"
            >
              Export JSON
            </button>
          </div>
        </div>

        {/* 1. Executive Report Overview */}
        <ReportHeader report={report} onBack={onBack} />

        {/* 2. Interactive Claim Investigation Grid */}
        <div className="mt-6 grid lg:grid-cols-[300px_1fr] gap-6 items-start">
          {/* Left Column: Claims Checklist */}
          <div className="lg:sticky lg:top-[90px]">
            <ClaimRail
              report={report}
              activeId={claim ? claim.id : ""}
              onSelect={setActiveId}
            />
          </div>

          {/* Right Column: Claim Inspection Details */}
          <div className="min-w-0">
            {claim ? (
              <ClaimDetail
                report={report}
                claim={claim}
                onCopy={() => {}}
                onSource={setSource}
              />
            ) : (
              <div className="rounded-2xl border border-black/10 bg-white p-8 text-center text-black/60">
                No checkable claims found. Click "+ Verify New Article" to analyze text.
              </div>
            )}
          </div>
        </div>

        {/* 3. Minimal Clean Footer Line */}
        <div className="mt-10 pt-5 border-t border-black/10 flex flex-wrap items-center justify-between gap-4 text-[12px] text-black/50">
          <div className="flex items-center gap-4">
            <span className="font-mono text-[11px]">
              Run {report.run.run_id} · Schema {report.schema_version}
            </span>
            <span>·</span>
            <a href="#method" className="hover:text-black hover:underline">
              Methodology Equation
            </a>
            <span>·</span>
            <a href="#limits" className="hover:text-black hover:underline">
              System Boundaries
            </a>
            <span>·</span>
            <a href="#audit" className="hover:text-black hover:underline">
              Full Audit Logs
            </a>
          </div>

          <button
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            className="text-black/60 hover:text-black transition-colors"
          >
            Back to top ↑
          </button>
        </div>
      </div>

      {/* Paste Text or URL Modal */}
      {showInputModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200"
          onClick={() => setShowInputModal(false)}
        >
          <div
            className="w-full max-w-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <VerifyInput
              onReportReady={(newReport) => {
                setShowInputModal(false);
                if (onCustomReport) {
                  onCustomReport(newReport);
                }
              }}
              onCancel={() => setShowInputModal(false)}
            />
          </div>
        </div>
      )}

      {/* Source Reader Modal */}
      {source && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm transition-opacity"
          onClick={() => setSource(null)}
        >
          <div
            className="h-full w-full max-w-[580px] overflow-y-auto bg-white p-6 sm:p-8 shadow-2xl flex flex-col justify-between"
            onClick={(e) => e.stopPropagation()}
          >
            <div>
              <div className="flex items-center justify-between border-b border-black/10 pb-4">
                <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-black/50">
                  Primary Source Record
                </span>
                <button
                  onClick={() => setSource(null)}
                  className="rounded-full w-7 h-7 flex items-center justify-center hover:bg-black/5 transition-colors text-black/60 hover:text-black font-mono text-[15px]"
                  aria-label="Close source view"
                >
                  ✕
                </button>
              </div>

              <div className="mt-5">
                <div className="flex items-center gap-2 mb-2">
                  <span className="rounded bg-black/[0.06] px-2.5 py-0.5 text-[11px] font-mono font-medium text-black/75">
                    {source.domain}
                  </span>
                  <span className="text-[12px] text-black/50">
                    {source.kind} · {source.date}
                  </span>
                </div>

                <h3
                  className="text-[22px] font-medium leading-snug tracking-tight text-black"
                  style={{ fontFamily: "var(--font-heading)" }}
                >
                  {source.title}
                </h3>

                <div className="mt-3">
                  <span
                    className={`inline-block rounded-full px-3 py-0.5 text-[10.5px] font-bold uppercase tracking-wider ${
                      source.stance === "SUPPORTS"
                        ? "bg-[#ecfdf5] text-[#065f46] border border-[#a7f3d0]"
                        : source.stance === "REFUTES"
                        ? "bg-[#fef2f2] text-[#991b1b] border border-[#fecaca]"
                        : "bg-[#fffbeb] text-[#92400e] border border-[#fde68a]"
                    }`}
                  >
                    Stance: {source.stance}
                  </span>
                </div>

                <div className="mt-5 rounded-xl border border-black/10 bg-[#fbfbfa] p-4">
                  <div className="flex items-center gap-1.5 text-[10.5px] font-semibold text-[#1f5740] font-mono mb-2">
                    <span className="w-2 h-2 rounded-full bg-[#1f5740]" />
                    Verified Quotation in Source Document:
                  </div>
                  <blockquote className="text-[14px] leading-relaxed text-black/85 italic border-l-2 border-[#1f5740] pl-3">
                    &ldquo;{source.quote}&rdquo;
                  </blockquote>
                </div>
              </div>
            </div>

            <div className="mt-8 pt-4 border-t border-black/10 flex items-center justify-between">
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="rounded-full bg-black text-white px-5 py-2 text-[12.5px] font-medium hover:bg-black/80 transition-colors inline-flex items-center gap-1.5"
              >
                <span>Open Original URL</span>
                <span className="text-[11px]">↗</span>
              </a>
              <button
                onClick={() => setSource(null)}
                className="rounded-full border border-black/20 px-4 py-2 text-[12.5px] text-black hover:bg-black hover:text-white transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
