import { useEffect, useState } from "react";
import Navbar, { type NavView } from "./components/Navbar";
import DistillHero from "./components/DistillHero";
import ReportView from "./components/ReportView";
import MethodSheet from "./components/MethodSheet";
import LimitsSheet from "./components/LimitsSheet";
import AuditView from "./components/AuditView";
import VerifyInput from "./components/VerifyInput";
import supported from "./data/mock_supported.json";
import refuted from "./data/mock_refuted.json";
import type { Report } from "./types";

const FIXTURES: Record<"moon" | "plunge", Report> = {
  moon: supported as unknown as Report,
  plunge: refuted as unknown as Report,
};

function parseHashView(): NavView {
  const hash = window.location.hash.replace("#", "").toLowerCase();
  if (hash === "report") return "report";
  if (hash === "method") return "method";
  if (hash === "limits") return "limits";
  if (hash === "audit") return "audit";
  return "home";
}

export default function App() {
  const [view, setView] = useState<NavView>(parseHashView);
  const [fixture, setFixture] = useState<"moon" | "plunge" | "custom">("moon");
  const [customReport, setCustomReport] = useState<Report | null>(null);
  const [showGlobalVerify, setShowGlobalVerify] = useState(false);

  // Keep view state in sync with URL hash for true link-based navigation
  useEffect(() => {
    const handleHash = () => {
      const nextView = parseHashView();
      setView(nextView);
      window.scrollTo({ top: 0, behavior: "smooth" });
    };

    window.addEventListener("hashchange", handleHash);
    return () => window.removeEventListener("hashchange", handleHash);
  }, []);

  const navigateTo = (target: NavView) => {
    setView(target);
    window.location.hash = target;
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleOpenReport = (key: "moon" | "plunge") => {
    setFixture(key);
    navigateTo("report");
  };

  const handleCustomReport = (newReport: Report) => {
    setCustomReport(newReport);
    setFixture("custom");
    setShowGlobalVerify(false);
    navigateTo("report");
  };

  // Determine current active report
  const activeReport: Report =
    fixture === "custom" && customReport
      ? customReport
      : FIXTURES[fixture === "plunge" ? "plunge" : "moon"];

  return (
    <div className="relative min-h-screen bg-[var(--bg)] text-[var(--body)] font-sans selection:bg-[#ffb06a]/30">
      {/* Adaptive Theme Navbar with +Verify Action */}
      <Navbar
        currentView={view}
        onNavigate={navigateTo}
        onOpenVerify={() => setShowGlobalVerify(true)}
      />

      {/* View routing with distinct, beautiful UI for each option */}
      {view === "home" && (
        <DistillHero
          onOpenReport={handleOpenReport}
          onOpenMethod={() => navigateTo("method")}
          onOpenLimits={() => navigateTo("limits")}
          onOpenAudit={() => navigateTo("audit")}
          onCustomReport={handleCustomReport}
        />
      )}

      {view === "report" && (
        <ReportView
          report={activeReport}
          fixture={fixture}
          onSelectFixture={(f) => setFixture(f)}
          onCustomReport={handleCustomReport}
          onBack={() => navigateTo("home")}
        />
      )}

      {view === "method" && (
        <MethodSheet
          onClose={() => navigateTo("home")}
          onOpenReport={() => handleOpenReport("moon")}
        />
      )}

      {view === "limits" && (
        <LimitsSheet
          onOpenReport={() => handleOpenReport("moon")}
          onOpenMethod={() => navigateTo("method")}
        />
      )}

      {view === "audit" && (
        <AuditView
          report={activeReport}
          fixture={fixture === "custom" ? "moon" : fixture}
          onSelectFixture={(f) => setFixture(f)}
          onOpenReport={() => navigateTo("report")}
        />
      )}

      {/* Global Verify Input Modal */}
      {showGlobalVerify && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in duration-200"
          onClick={() => setShowGlobalVerify(false)}
        >
          <div
            className="w-full max-w-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <VerifyInput
              onReportReady={handleCustomReport}
              onCancel={() => setShowGlobalVerify(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
