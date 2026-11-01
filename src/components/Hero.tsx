import React, { memo, useEffect, useState } from "react";
import { useTypewriter } from "../hooks/useTypewriter";
import VerifyInput from "./VerifyInput";
import type { Report } from "../types";

const TYPEWRITER_TEXT =
  "Glad you stopped in. Good taste tends to find us. Now, what are we building?";

interface Props {
  onOpenReport: (key: "moon" | "plunge") => void;
  onOpenMethod: () => void;
  onOpenAudit: () => void;
  onCustomReport: (report: Report) => void;
}

/* Memoized typed line to avoid re-rendering unrelated sibling nodes during typing */
const TypedLine = memo(function TypedLine({ text }: { text: string }) {
  const { displayed, done } = useTypewriter(text, 38, 600);

  return (
    <p
      className="text-black mb-5 sm:mb-6 font-normal"
      style={{
        fontSize: "clamp(18px, 4vw, 26px)",
        lineHeight: 1.35,
        minHeight: 54,
      }}
    >
      {displayed}
      {!done && (
        <span
          className="inline-block w-[2px] h-[1.1em] bg-black align-middle ml-[2px]"
          style={{ animation: "blink 1s step-end infinite" }}
          aria-hidden="true"
        />
      )}
    </p>
  );
});

export default function Hero({
  onOpenReport,
  onOpenMethod,
  onOpenAudit,
  onCustomReport,
}: Props) {
  const [pillsVisible, setPillsVisible] = useState(false);
  const [showInputModal, setShowInputModal] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setPillsVisible(true);
    }, 400);

    return () => clearTimeout(timer);
  }, []);

  const copyEmail = async () => {
    try {
      await navigator.clipboard.writeText("hello@factora.co");
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className="relative z-[1] h-screen flex flex-col justify-end md:justify-center pb-12 md:pb-0 px-5 sm:px-8 md:px-10 overflow-hidden">
      <div className="relative z-10 max-w-xl">
        {/* 1. Blurred intro label */}
        <div
          className="pointer-events-none select-none mb-5 sm:mb-6 font-normal text-black"
          style={{
            fontSize: "clamp(18px, 4vw, 26px)",
            lineHeight: 1.3,
            filter: "blur(4px)",
          }}
          aria-hidden="true"
        >
          Hey there, meet A.R.I.A,
          <br />
          Factora's Adaptive Response Interface Agent
        </div>

        {/* 2. Typewriter text */}
        <TypedLine text={TYPEWRITER_TEXT} />

        {/* 3. Action pill buttons */}
        <div
          className="flex flex-wrap gap-y-1"
          style={{
            opacity: pillsVisible ? 1 : 0,
            transform: pillsVisible ? "translateY(0)" : "translateY(8px)",
            transition: "opacity 0.4s ease, transform 0.4s ease",
          }}
        >
          {/* Custom Paste Text or URL Pill (Primary Action) */}
          <button
            onClick={() => setShowInputModal(true)}
            className="inline-flex items-center justify-center bg-black text-white border border-black rounded-full text-[13px] sm:text-[15px] px-5 sm:px-6 py-[0.35em] mx-[0.2em] mb-[0.4em] whitespace-nowrap hover:bg-black/80 transition-all font-medium shadow-sm"
          >
            <span>Paste Text or URL</span>
            <span className="ml-1.5 text-[12px]">↗</span>
          </button>

          {/* Quick preset article pills */}
          <a
            href="#report"
            onClick={(e) => {
              e.preventDefault();
              onOpenReport("moon");
            }}
            className="inline-flex items-center justify-center bg-white text-black border border-black/10 rounded-full text-[13px] sm:text-[15px] px-4 sm:px-5 py-[0.3em] mx-[0.2em] mb-[0.4em] whitespace-nowrap hover:bg-black hover:text-white transition-colors duration-200"
          >
            Apollo 11 Report
          </a>

          <a
            href="#report"
            onClick={(e) => {
              e.preventDefault();
              onOpenReport("plunge");
            }}
            className="inline-flex items-center justify-center bg-white text-black border border-black/10 rounded-full text-[13px] sm:text-[15px] px-4 sm:px-5 py-[0.3em] mx-[0.2em] mb-[0.4em] whitespace-nowrap hover:bg-black hover:text-white transition-colors duration-200"
          >
            Market Plunge Report
          </a>

          <a
            href="#method"
            onClick={(e) => {
              e.preventDefault();
              onOpenMethod();
            }}
            className="inline-flex items-center justify-center bg-white text-black border border-black/10 rounded-full text-[13px] sm:text-[15px] px-4 sm:px-5 py-[0.3em] mx-[0.2em] mb-[0.4em] whitespace-nowrap hover:bg-black hover:text-white transition-colors duration-200"
          >
            Scoring Method
          </a>

          <a
            href="#audit"
            onClick={(e) => {
              e.preventDefault();
              onOpenAudit();
            }}
            className="inline-flex items-center justify-center bg-white text-black border border-black/10 rounded-full text-[13px] sm:text-[15px] px-4 sm:px-5 py-[0.3em] mx-[0.2em] mb-[0.4em] whitespace-nowrap hover:bg-black hover:text-white transition-colors duration-200"
          >
            Audit Log
          </a>

          {/* Outline email copy button */}
          <button
            onClick={copyEmail}
            className="inline-flex items-center justify-center text-white bg-transparent border border-white rounded-full text-[13px] sm:text-[15px] px-4 sm:px-5 py-[0.3em] mx-[0.2em] mb-[0.4em] whitespace-nowrap gap-2 sm:gap-3 hover:bg-white hover:text-black transition-colors duration-200"
            title="Click to copy email address"
          >
            <span>
              Reach us:{" "}
              <span className="underline underline-offset-1">
                hello@factora.co
              </span>
            </span>
            <svg
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              aria-hidden="true"
            >
              <rect
                x="3.5"
                y="0.5"
                width="8"
                height="8"
                rx="1"
                stroke="currentColor"
              />
              <rect
                x="0.5"
                y="3.5"
                width="8"
                height="8"
                rx="1"
                stroke="currentColor"
              />
            </svg>
            <span className="sr-only">
              {copied ? "Copied" : "Copy email address"}
            </span>
          </button>
        </div>

        {/* Subtle scrubber status */}
        <p
          className="mt-3 text-[10px] tracking-[0.16em] uppercase text-black/50 select-none"
          style={{
            opacity: pillsVisible ? 1 : 0,
            transition: "opacity 0.4s ease 0.15s",
          }}
        >
          {copied
            ? "✓ Email copied to clipboard"
            : "Move cursor horizontally to scrub video frame"}
        </p>
      </div>

      {/* Interactive Paste Text / URL Modal */}
      {showInputModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in duration-200"
          onClick={() => setShowInputModal(false)}
        >
          <div
            className="w-full max-w-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <VerifyInput
              onReportReady={(newReport) => {
                setShowInputModal(false);
                onCustomReport(newReport);
              }}
              onCancel={() => setShowInputModal(false)}
            />
          </div>
        </div>
      )}
    </section>
  );
}
