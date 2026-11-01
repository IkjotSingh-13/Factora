import React, { useEffect, useRef, useState } from "react";
import VerifyInput from "./VerifyInput";
import type { Report } from "../types";

interface Props {
  onOpenReport: (key: "moon" | "plunge") => void;
  onOpenMethod: () => void;
  onOpenLimits: () => void;
  onOpenAudit: () => void;
  onCustomReport: (report: Report) => void;
}

export interface FactoraCardMeta {
  id: string;
  claimId: string;
  title: string;
  origin: string;
  articleQuote: string;
  stance: "SUPPORTED" | "REFUTED" | "CONTESTED";
  score: number;
  scoreBreakdown: { c: number; r: number; a: number; t: number; k: number };
  quoteVerified: boolean;
  sources: string[];
  verdict: string;
  fixtureKey: "moon" | "plunge";
  bgImage: string;
}

export const FACTORA_CARDS: FactoraCardMeta[] = [
  {
    id: "plunge",
    claimId: "CLM-5502",
    title: "The Cold Plunge Numbers Nobody Checks",
    origin: "wellness-pulse.co · 194 words",
    articleQuote:
      "A cold plunge promises the metabolic effect of a long run in fifteen minutes.",
    stance: "REFUTED",
    score: 0.84,
    scoreBreakdown: { c: 0.88, r: 0.92, a: 0.8, t: 0.75, k: 0.85 },
    quoteVerified: true,
    sources: [
      "pubmed.ncbi.nlm.nih.gov",
      "sportsinstitute.org",
      "health.harvard.edu",
    ],
    verdict:
      "Metabolic spike is transient cold-shock shivering; zero endurance cardiovascular equivalence.",
    fixtureKey: "plunge",
    bgImage:
      "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/156ff74d-ee37-4443-8f3a-11ae2095ed4c.webp",
  },
  {
    id: "moon",
    claimId: "CLM-4417",
    title: "Lunar South Pole Water Ice Discovery",
    origin: "science.nasa.gov · 236 words",
    articleQuote:
      "Water ice exists in permanently shadowed craters at the lunar south pole.",
    stance: "SUPPORTED",
    score: 0.94,
    scoreBreakdown: { c: 0.95, r: 0.96, a: 0.92, t: 0.9, k: 0.94 },
    quoteVerified: true,
    sources: ["science.nasa.gov", "eso.org", "en.wikipedia.org"],
    verdict:
      "Mini-RF radar reflections and LCROSS plume spectroscopy confirm crater floor volatiles.",
    fixtureKey: "moon",
    bgImage:
      "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/0e201fcd-ffc0-46a0-bd06-faa07bf0e84b.webp",
  },
  {
    id: "super",
    claimId: "CLM-9921",
    title: "Room-Temperature Superconductor LK-99",
    origin: "arxiv-viral.net · 410 words",
    articleQuote:
      "Modified lead-apatite crystals demonstrate zero resistance and levitation at room temperature.",
    stance: "CONTESTED",
    score: 0.42,
    scoreBreakdown: { c: 0.65, r: 0.85, a: 0.15, t: 0.95, k: 0.3 },
    quoteVerified: false,
    sources: ["nature.com", "mpis.mpg.de", "arxiv.org"],
    verdict:
      "Ferromagnetic Cu2S phase transition explains levitation; independent labs detect finite resistance.",
    fixtureKey: "moon",
    bgImage:
      "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/2bebad68-8177-446a-9550-d64074166364.webp",
  },
  {
    id: "fasting",
    claimId: "CLM-3310",
    title: "16-Hour Fasting Triggers Cellular Autophagy",
    origin: "biohack-digest.org · 210 words",
    articleQuote:
      "Skipping breakfast initiates deep cellular rejuvenation and eliminates damaged organelles within 16 hours.",
    stance: "REFUTED",
    score: 0.89,
    scoreBreakdown: { c: 0.92, r: 0.94, a: 0.85, t: 0.8, k: 0.9 },
    quoteVerified: true,
    sources: [
      "cell.com/cell-metabolism",
      "ncbi.nlm.nih.gov",
      "clinicaltrials.gov",
    ],
    verdict:
      "Rodent fasting kinetics erroneously extrapolated to humans; hepatic autophagy requires 48-72h.",
    fixtureKey: "plunge",
    bgImage:
      "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/e80ba08d-d7b6-4b27-b0cd-8d0ad09c788c.webp",
  },
  {
    id: "mrna",
    claimId: "CLM-8842",
    title: "Personalized mRNA Melanoma Vaccine Trial",
    origin: "lancet-oncology.com · 315 words",
    articleQuote:
      "Adjuvant mRNA-4157 combined with pembrolizumab reduced recurrence or death by 44% in resected stage III/IV melanoma.",
    stance: "SUPPORTED",
    score: 0.95,
    scoreBreakdown: { c: 0.98, r: 0.98, a: 0.94, t: 0.92, k: 0.95 },
    quoteVerified: true,
    sources: [
      "thelancet.com",
      "clinicaltrials.gov/NCT03897881",
      "fda.gov",
    ],
    verdict:
      "Randomized phase IIb trial confirms statistically significant recurrence-free survival (HR 0.561).",
    fixtureKey: "moon",
    bgImage:
      "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/62b0d7c1-3eb7-44bd-9b19-57b975fb250f.webp",
  },
  {
    id: "ocean",
    claimId: "CLM-7719",
    title: "Global Ocean Heat Content 0-2000m Sets Record",
    origin: "copernicus.eu · 280 words",
    articleQuote:
      "Upper 2000m ocean heat content reached the highest level in observational history, absorbing 15 zettajoules.",
    stance: "SUPPORTED",
    score: 0.93,
    scoreBreakdown: { c: 0.96, r: 0.94, a: 0.95, t: 0.9, k: 0.92 },
    quoteVerified: true,
    sources: ["ncei.noaa.gov", "copernicus.eu", "argo.ucsd.edu"],
    verdict:
      "Multi-agency sensor synthesis (3,800 autonomous profiling floats) independently verifies anomaly.",
    fixtureKey: "moon",
    bgImage:
      "https://d2ol7oe51mr4n9.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/7b6ad638-89ec-4dec-be74-d36fbff3ea53.webp",
  },
];

const PALETTE = [
  "#FF4A12",
  "#FF5A1F",
  "#FF6B2C",
  "#FF8A3D",
  "#FFB06A",
  "#FFD9B5",
  "#FFF3E6",
];

// 32-bit integer hash function
function hash(a: number, b: number, c: number): number {
  let h =
    Math.imul(a, 374761393) +
    Math.imul(b, 668265263) +
    Math.imul(c, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

interface Spark {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  color: string;
  maxLife: number;
  life: number;
}

export default function DistillHero({
  onOpenReport,
  onOpenMethod,
  onOpenLimits,
  onOpenAudit,
  onCustomReport,
}: Props) {
  const beltRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const badgeRef = useRef<HTMLDivElement>(null);

  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);
  const codeRefs = useRef<(HTMLDivElement | null)[]>([]);

  const [showVerifyModal, setShowVerifyModal] = useState(false);
  const [displayedScore, setDisplayedScore] = useState<number>(0.94);
  const [displayedStance, setDisplayedStance] = useState<
    "SUPPORTED" | "REFUTED" | "CONTESTED"
  >("SUPPORTED");
  const [displayedClaim, setDisplayedClaim] = useState<string>("CLM-4417");

  useEffect(() => {
    const belt = beltRef.current;
    const canvas = canvasRef.current;
    const badge = badgeRef.current;
    if (!belt || !canvas || !badge) return;

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    // Responsive geometry state
    let cw = 0;
    let ch = 0;
    let gap = 0;
    let beltTop = 0;
    let pitch = 0;
    let totalW = 0;
    let dpr = 1;
    let Lx = window.innerWidth / 2;

    const updateGeometry = () => {
      const W = window.innerWidth;
      const H = window.innerHeight;
      const isMobile = W < 768;

      cw = isMobile ? W * 0.84 : W * 0.46;
      ch = isMobile ? W * 0.54 : H * 0.52;
      gap = isMobile ? W * 0.07 : W * 0.0475;
      beltTop = isMobile ? H * 0.54 : H * 0.402;
      pitch = cw + gap;
      totalW = 6 * pitch;
      Lx = W / 2;

      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = `${W}px`;
      canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      badge.style.top = `${beltTop - 54}px`;
    };

    updateGeometry();
    window.addEventListener("resize", updateGeometry);

    // Physics & Motion State
    let pos = 0;
    let baseSpeed = -0.075 * window.innerWidth;
    let speed = baseSpeed;
    let vel = 0;
    let isDragging = false;
    let isHovered = false;
    let dragStartX = 0;
    let dragStartPos = 0;
    let lastDragX = 0;
    let lastDragTime = 0;
    let dragDistance = 0;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    if (reducedMotion) {
      baseSpeed = 0;
      speed = 0;
    }

    // Pointer events
    const onPointerDown = (e: PointerEvent) => {
      isDragging = true;
      dragStartX = e.clientX;
      dragStartPos = pos;
      lastDragX = e.clientX;
      lastDragTime = performance.now();
      dragDistance = 0;
      vel = 0;
      belt.classList.add("is-dragging");
      belt.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!isDragging) return;
      const dx = e.clientX - dragStartX;
      dragDistance += Math.abs(e.clientX - lastDragX);
      pos = dragStartPos + dx;

      const now = performance.now();
      const dtMs = now - lastDragTime;
      if (dtMs > 0) {
        vel = ((e.clientX - lastDragX) / dtMs) * 1000;
      }
      lastDragX = e.clientX;
      lastDragTime = now;
    };

    const onPointerUp = (e: PointerEvent) => {
      if (!isDragging) return;
      isDragging = false;
      belt.classList.remove("is-dragging");
      try {
        belt.releasePointerCapture(e.pointerId);
      } catch {
        // ignore
      }
    };

    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        pos -= e.deltaX;
      }
    };

    const onPointerEnter = () => {
      isHovered = true;
    };
    const onPointerLeave = () => {
      isHovered = false;
    };

    belt.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    belt.addEventListener("wheel", onWheel, { passive: false });
    belt.addEventListener("pointerenter", onPointerEnter);
    belt.addEventListener("pointerleave", onPointerLeave);

    // Free sparks array
    const sparks: Spark[] = [];

    // Main animation loop
    let lastT = performance.now();
    let currentScore = 0.94;
    let animId = 0;

    const loop = (now: number) => {
      const dt = Math.min((now - lastT) / 1000, 0.05);
      lastT = now;
      const t = now / 1000;

      // Update position
      if (isDragging) {
        // user controls pos directly
      } else {
        if (Math.abs(vel) > 1) {
          pos += vel * dt;
          vel *= Math.pow(0.04, dt);
        } else {
          vel = 0;
          baseSpeed = -0.075 * window.innerWidth;
          const targetSpeed = isHovered ? baseSpeed * 0.35 : baseSpeed;
          speed += (targetSpeed - speed) * Math.min(1, dt * 6);
          pos += speed * dt;
        }
      }

      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);

      let activeFound = false;

      // Render cards
      for (let i = 0; i < 6; i++) {
        const cardEl = cardRefs.current[i];
        const codeEl = codeRefs.current[i];
        if (!cardEl || !codeEl) continue;

        let x = ((((i * pitch + pos) % totalW) + totalW) % totalW) - 0.5 * pitch;
        if (x > totalW - pitch) x -= totalW;

        const cut = Math.max(0, Math.min(cw, Lx - x));

        // Card transform & clipping
        cardEl.style.transform = `translate3d(${x.toFixed(2)}px, 0, 0)`;
        if (cut > 0 && cut < cw) {
          cardEl.style.clipPath = `inset(0 0 0 ${cut.toFixed(2)}px)`;
          cardEl.style.maskImage = `linear-gradient(90deg, transparent ${cut.toFixed(2)}px, #000 ${(cut + 26).toFixed(2)}px)`;
          (cardEl.style as any).webkitMaskImage = `linear-gradient(90deg, transparent ${cut.toFixed(2)}px, #000 ${(cut + 26).toFixed(2)}px)`;
        } else {
          cardEl.style.clipPath = "none";
          cardEl.style.maskImage = "none";
          (cardEl.style as any).webkitMaskImage = "none";
        }

        // Code transform & clipping
        codeEl.style.transform = `translate3d(${x.toFixed(2)}px, 0, 0)`;
        codeEl.style.clipPath = `inset(0 ${(cw - cut).toFixed(2)}px 0 0)`;
        codeEl.style.visibility = cut === 0 ? "hidden" : "visible";

        // Check active card crossing scanner
        if (!activeFound && cut > 0 && cut < cw) {
          activeFound = true;
          const meta = FACTORA_CARDS[i];
          currentScore += (meta.score - currentScore) * Math.min(1, dt * 14);
          setDisplayedScore(currentScore);
          setDisplayedStance(meta.stance);
          setDisplayedClaim(meta.claimId);
        }

        // Canvas Dissolve FX (Haze & Particles)
        if (cut > 0 && x + cw > Lx) {
          const cardTop = 70;

          // 1. Haze
          const hazeW = Math.min(140, x + cw - Lx);
          if (hazeW > 0) {
            const hazeGrad = ctx.createLinearGradient(Lx, 0, Lx + 140, 0);
            hazeGrad.addColorStop(0, "rgba(255, 90, 31, 0.40)");
            hazeGrad.addColorStop(1, "rgba(255, 90, 31, 0)");
            ctx.fillStyle = hazeGrad;
            ctx.fillRect(Lx, cardTop, hazeW, ch);
          }

          // 2. Dithered Particles on 3px grid in card-local coordinates
          const R = 0.3 * cw;
          const startGx = Math.floor(cut / 3);
          const endGx = Math.min(Math.floor(cw / 3), Math.floor((cut + R) / 3));
          const numRows = Math.floor(ch / 3);

          for (let gx = startGx; gx <= endGx; gx++) {
            const sx = x + gx * 3;
            const d = sx - Lx;
            if (d < 0 || d > R) continue;

            const p = Math.pow(1 - d / R, 1.7);

            for (let row = -4; row <= numRows + 4; row++) {
              const isInside = row >= 0 && row <= numRows;
              const probLimit = isInside
                ? p * 0.92
                : p * 0.16 * (1 - Math.abs(row < 0 ? row : row - numRows) / 4);

              const hVal = hash(gx, row, i);
              if (hVal >= probLimit) continue;

              // Twinkle check (9Hz)
              if (!reducedMotion && hash(gx, row, Math.floor(t * 9) + 97) < 0.22) {
                continue;
              }

              // Color index based on distance to edge
              const dFrac = d / R;
              const colorIdx =
                dFrac < 0.08
                  ? 6
                  : dFrac < 0.18
                  ? 5
                  : dFrac < 0.32
                  ? 4
                  : dFrac < 0.5
                  ? 3
                  : dFrac < 0.7
                  ? 2
                  : dFrac < 0.88
                  ? 1
                  : 0;

              const px = sx + (hash(gx, row, 11) - 0.5) * 1.5;
              const py =
                cardTop + row * 3 + (hash(gx, row, 23) - 0.5) * 1.5;

              ctx.fillStyle = PALETTE[colorIdx];
              ctx.fillRect(px, py, 3, 3);

              // Spawn free sparks at edge
              if (
                !reducedMotion &&
                dFrac < 0.06 &&
                sparks.length < 90 &&
                Math.random() < 0.025
              ) {
                sparks.push({
                  x: px,
                  y: py,
                  vx: -25 - Math.random() * 55,
                  vy: (Math.random() - 0.5) * 45,
                  size: 2 + Math.random() * 2,
                  color: PALETTE[colorIdx],
                  maxLife: 0.3 + Math.random() * 0.8,
                  life: 0,
                });
              }
            }
          }
        }
      }

      // Update & render free sparks
      for (let s = sparks.length - 1; s >= 0; s--) {
        const sp = sparks[s];
        sp.life += dt;
        if (sp.life >= sp.maxLife) {
          sparks.splice(s, 1);
          continue;
        }

        sp.x += sp.vx * dt;
        sp.y += sp.vy * dt;
        const alpha = Math.min(1, (1 - sp.life / sp.maxLife) * 2.2);

        ctx.globalAlpha = alpha;
        ctx.fillStyle = sp.color;
        ctx.fillRect(sp.x, sp.y, sp.size, sp.size);
        ctx.globalAlpha = 1.0;
      }

      animId = requestAnimationFrame(loop);
    };

    animId = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", updateGeometry);
      belt.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      belt.removeEventListener("wheel", onWheel);
      belt.removeEventListener("pointerenter", onPointerEnter);
      belt.removeEventListener("pointerleave", onPointerLeave);
    };
  }, []);

  return (
    <div className="distill-stage">
      {/* 1. HERO COPY (Only Factora-relevant elements, padded below fixed Navbar) */}
      <div className="px-5 sm:px-[7.5vw] pt-[88px] sm:pt-[100px] grid md:grid-cols-[1.1fr_0.9fr] gap-6 md:gap-[4.5vw] relative z-20">
        {/* Left Column */}
        <div>
          <div
            className="text-[11.5px] font-semibold uppercase tracking-[0.12em] text-[#FF5A1F] flex items-center gap-2"
            style={{ fontFamily: "var(--mono)" }}
          >
            <span className="w-2 h-2 rounded-full bg-[#FF5A1F] animate-pulse" />
            <span>FACTORA · AUTOMATED CLAIM EXTRACTION & EVIDENCE AUDITING</span>
          </div>
          <h1
            className="mt-3 text-[32px] sm:text-[clamp(32px,3.2vw,56px)] leading-[1.06] tracking-[-0.028em] text-[#0F1115]"
            style={{ fontFamily: "var(--serif)" }}
          >
            Built for truth.
            <br />
            <em className="italic">Verified by evidence.</em>
          </h1>
        </div>

        {/* Right Column */}
        <div className="flex flex-col justify-start items-start">
          <p
            className="text-[14px] sm:text-[clamp(14px,1.05vw,17.5px)] leading-[1.55] text-[#2A2E35] max-w-xl"
            style={{ fontFamily: "var(--sans)" }}
          >
            Factora extracts factual claims from online content, checks exact
            quotes against peer-reviewed registries, and calculates transparent
            evidence scores—never an unsupported AI verdict.
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              onClick={() => setShowVerifyModal(true)}
              className="h-[38px] px-5 rounded-full bg-[#FF5A1F] text-white font-medium text-[13px] hover:-translate-y-[1px] hover:bg-[#e04810] transition-all duration-200 shadow-sm inline-flex items-center gap-2"
              style={{ fontFamily: "var(--sans)" }}
            >
              <span>+ Verify a Claim or URL</span>
              <span className="text-[12px]">↗</span>
            </button>

            <button
              onClick={() => onOpenReport("moon")}
              className="h-[38px] px-4 rounded-full border border-black/20 bg-white/70 hover:bg-white text-black font-medium text-[13px] transition-all"
            >
              View Demo Report
            </button>

            <button
              onClick={onOpenMethod}
              className="text-[12px] text-black/70 hover:text-black underline underline-offset-4 font-mono font-medium"
            >
              S = 0.30C + 0.30R...
            </button>
          </div>
        </div>
      </div>

      {/* 2. THE CONVEYOR BELT */}
      <div ref={beltRef} className="distill-belt">
        {/* Layer 1: Code Layer (.codes) */}
        <div className="distill-codes">
          {FACTORA_CARDS.map((meta, i) => (
            <div
              key={`code-${meta.id}`}
              ref={(el) => (codeRefs.current[i] = el)}
              onClick={() => onOpenReport(meta.fixtureKey)}
              className="card-shell distill-code bg-[#14161B] text-[#8C939E] p-[3cqw] border border-black/20 cursor-pointer"
            >
              <div className="relative w-full h-full flex flex-col justify-between">
                {/* Structured Evidence Code */}
                <div className="distill-code-text text-[#8C939E] overflow-hidden leading-[1.4] select-text">
                  <div className="text-[#FF5A1F] font-semibold mb-1">
                    {`<!-- FACTORA EVIDENCE AUDIT: ${meta.claimId} -->`}
                  </div>
                  <div>
                    {`<claim_analysis id="${meta.claimId}">`}
                  </div>
                  <div className="pl-3">
                    {`<stance status="${meta.stance}">`}
                    <span
                      className={`ml-1 font-bold ${
                        meta.stance === "SUPPORTED"
                          ? "text-emerald-400"
                          : meta.stance === "REFUTED"
                          ? "text-rose-400"
                          : "text-amber-400"
                      }`}
                    >
                      {meta.stance}
                    </span>
                    {`</stance>`}
                  </div>
                  <div className="pl-3">
                    {`<evidence_strength S="${meta.score.toFixed(2)}">`}
                  </div>
                  <div className="pl-6 text-[#A0A6B2]">
                    {`<!-- formula: 0.30C + 0.30R + 0.20A + 0.10T + 0.10K -->\n`}
                    {`<components C="${meta.scoreBreakdown.c.toFixed(2)}" R="${meta.scoreBreakdown.r.toFixed(2)}" A="${meta.scoreBreakdown.a.toFixed(2)}" T="${meta.scoreBreakdown.t.toFixed(2)}" K="${meta.scoreBreakdown.k.toFixed(2)}" />`}
                  </div>
                  <div className="pl-3">{`</evidence_strength>`}</div>
                  <div className="pl-3 text-emerald-400">
                    {`<quote_guard substring_verified="${meta.quoteVerified}" />`}
                  </div>
                  <div className="pl-3">
                    {`<sources matched="${meta.sources.length}">`}
                  </div>
                  {meta.sources.map((s, idx) => (
                    <div key={idx} className="pl-6 text-[#B0B7C3]">
                      {`<source registry="${s}" status="VERIFIED" />`}
                    </div>
                  ))}
                  <div className="pl-3">{`</sources>`}</div>
                  <div className="pl-3 text-white/90">
                    {`<verdict>${meta.verdict}</verdict>`}
                  </div>
                  <div>{`</claim_analysis>`}</div>
                </div>

                {/* Footer annotation */}
                <div className="pt-2 border-t border-white/10 flex items-center justify-between text-[1.4cqw] font-mono text-[#FF5A1F]">
                  <span>100% AUDITED EVIDENCE</span>
                  <span>CLICK CARD TO OPEN FULL REPORT ↗</span>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Layer 2: Website Cards Layer (.cards) */}
        <div className="distill-cards">
          {FACTORA_CARDS.map((meta, i) => (
            <div
              key={`card-${meta.id}`}
              ref={(el) => (cardRefs.current[i] = el)}
              onClick={() => onOpenReport(meta.fixtureKey)}
              className="card-shell cursor-pointer border border-white/20 shadow-xl overflow-hidden"
              style={{
                backgroundImage: `linear-gradient(180deg, rgba(14,16,20,0.72) 0%, rgba(14,16,20,0.40) 45%, rgba(14,16,20,0.92) 100%), url('${meta.bgImage}')`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }}
            >
              {/* Header Badge */}
              <div className="absolute left-[4cqw] right-[4cqw] top-[3.8cqw] flex items-center justify-between text-white">
                <div className="flex items-center gap-[1.5cqw]">
                  <span className="w-[1.6cqw] h-[1.6cqw] rounded-full bg-[#FF5A1F] animate-ping" />
                  <span className="text-[1.8cqw] font-mono tracking-wider text-white/90 uppercase">
                    UNVERIFIED ARTICLE · {meta.origin}
                  </span>
                </div>
                <div className="px-[2cqw] py-[0.6cqw] rounded-full bg-black/60 border border-white/20 text-[1.5cqw] font-mono text-white/80">
                  {meta.claimId}
                </div>
              </div>

              {/* Title & Quoted Assertion */}
              <div className="absolute left-[4cqw] right-[4cqw] top-[14cqw]">
                <h3
                  className="text-[4cqw] font-semibold text-white leading-[1.12] tracking-[-0.02em] line-clamp-2"
                  style={{ fontFamily: "var(--serif)" }}
                >
                  {meta.title}
                </h3>

                <div className="mt-[2.5cqw] p-[2.5cqw] rounded-xl bg-black/40 backdrop-blur-md border border-white/15">
                  <div className="text-[1.3cqw] font-mono uppercase tracking-widest text-[#FFB06A] mb-[0.8cqw]">
                    VERBATIM EXTRACT QUOTE
                  </div>
                  <p
                    className="text-[2.2cqw] leading-[1.38] text-white/95 italic font-light"
                    style={{ fontFamily: "var(--sans)" }}
                  >
                    "{meta.articleQuote}"
                  </p>
                </div>
              </div>

              {/* Footer Meta */}
              <div className="absolute left-[4cqw] right-[4cqw] bottom-[3.5cqw] flex items-center justify-between text-white/80 border-t border-white/15 pt-[2.2cqw]">
                <div className="flex items-center gap-[2cqw] text-[1.6cqw] font-mono">
                  <span className="text-[#FFB06A]">VERIFYING ADAPTERS:</span>
                  <span>{meta.sources.length} SOURCES</span>
                </div>
                <div className="text-[1.6cqw] font-medium text-white hover:text-[#FF5A1F] transition-colors flex items-center gap-[0.8cqw]">
                  <span>Click to view evidence breakdown</span>
                  <span>↗</span>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Layer 3: Particle Dissolve Canvas */}
        <canvas ref={canvasRef} className="distill-canvas" />

        {/* Layer 4: Vertical Scanner Line */}
        <div className="distill-scan">
          <div className="distill-scan-core" />
          <div className="distill-scan-halo" />
        </div>

        {/* Layer 5: Floating Counter Badge */}
        <div ref={badgeRef} className="distill-badge">
          <div className="distill-badge-label">
            EVIDENCE STRENGTH · S SCORE
          </div>
          <div className="distill-badge-pill">
            <span className="text-[#FF5A1F] font-bold">
              S = {displayedScore.toFixed(2)}
            </span>
            <span className="text-white/40">|</span>
            <span
              className={`font-semibold uppercase tracking-wider text-[11px] ${
                displayedStance === "SUPPORTED"
                  ? "text-emerald-400"
                  : displayedStance === "REFUTED"
                  ? "text-rose-400"
                  : "text-amber-400"
              }`}
            >
              {displayedStance}
            </span>
            <span className="text-white/40">|</span>
            <span className="text-white/70 text-[11px] font-mono">
              {displayedClaim}
            </span>
          </div>
        </div>
      </div>

      {/* Verify Modal for Custom Text or URL */}
      {showVerifyModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200"
          onClick={() => setShowVerifyModal(false)}
        >
          <div
            className="w-full max-w-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <VerifyInput
              onReportReady={(rep) => {
                setShowVerifyModal(false);
                onCustomReport(rep);
              }}
              onCancel={() => setShowVerifyModal(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
