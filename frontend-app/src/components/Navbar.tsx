import React, { useState } from "react";

export type NavView = "home" | "report" | "method" | "limits" | "audit";

interface Props {
  currentView: NavView;
  onNavigate?: (view: NavView) => void;
  onOpenVerify?: () => void;
}

interface NavItem {
  id: NavView;
  label: string;
  href: string;
}

const NAV_LINKS: NavItem[] = [
  { id: "report", label: "Report", href: "#report" },
  { id: "method", label: "Method", href: "#method" },
  { id: "limits", label: "Limits", href: "#limits" },
  { id: "audit", label: "Audit", href: "#audit" },
];

export default function Navbar({ currentView, onNavigate, onOpenVerify }: Props) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const isHome = currentView === "home";

  const handleLinkClick = (view: NavView) => {
    setMobileOpen(false);
    if (onNavigate) {
      onNavigate(view);
    }
  };

  const handleVerifyClick = () => {
    setMobileOpen(false);
    if (onOpenVerify) {
      onOpenVerify();
    }
  };

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-30 px-3 sm:px-6 pt-3 sm:pt-4 pointer-events-none transition-all duration-300">
        <div
          className={`pointer-events-auto mx-auto flex max-w-[1240px] items-center justify-between gap-3 rounded-full border px-4 sm:px-6 py-2.5 transition-all duration-300 ${
            isHome
              ? "bg-white/75 backdrop-blur-md border-black/10 shadow-[0_10px_35px_rgba(0,0,0,0.08)]"
              : "bg-[#f4f1ea]/95 backdrop-blur-md border-black/20 shadow-[0_8px_30px_rgba(19,17,16,0.07)]"
          }`}
        >
          {/* Logo (left) */}
          <a
            href="#home"
            onClick={() => handleLinkClick("home")}
            className="flex items-center gap-2.5 text-left focus:outline-none group shrink-0"
            aria-label="Factora Home"
          >
            <span
              className="text-[20px] sm:text-[23px] tracking-tight text-black leading-none group-hover:opacity-75 transition-opacity"
              style={{ fontFamily: "var(--font-heading)" }}
            >
              Factora®
            </span>
            <span
              className="text-[22px] sm:text-[25px] text-black select-none leading-none group-hover:rotate-45 transition-transform duration-300"
              style={{ letterSpacing: "-0.02em" }}
              aria-hidden="true"
            >
              ✳︎
            </span>
          </a>

          {/* Desktop Links (center) */}
          <nav className="hidden md:flex items-center gap-1 sm:gap-1.5 text-[14px] leading-none">
            {NAV_LINKS.map((link) => {
              const active = currentView === link.id;
              return (
                <a
                  key={link.id}
                  href={link.href}
                  onClick={() => handleLinkClick(link.id)}
                  className={`rounded-full px-3.5 py-1.5 font-medium transition-all duration-200 ${
                    active
                      ? "bg-black text-white shadow-sm"
                      : "text-black/70 hover:text-black hover:bg-black/[0.06]"
                  }`}
                  aria-current={active ? "page" : undefined}
                >
                  {link.label}
                </a>
              );
            })}
          </nav>

          {/* CTA & Actions (right) */}
          <div className="flex items-center gap-2">
            {/* Direct Paste / Verify button */}
            {onOpenVerify && (
              <button
                onClick={handleVerifyClick}
                className="rounded-full bg-[#FF5A1F] text-white px-4 py-1.5 text-[12.5px] sm:text-[13px] font-medium hover:bg-[#e04810] transition-all flex items-center gap-1.5 shadow-sm"
              >
                <span>+ Verify</span>
              </button>
            )}

            {/* Mobile Hamburger Toggle */}
            <button
              className="md:hidden flex flex-col justify-center items-center gap-[5px] w-9 h-9 rounded-full hover:bg-black/5 transition-colors focus:outline-none"
              onClick={() => setMobileOpen((prev) => !prev)}
              aria-label={mobileOpen ? "Close menu" : "Open menu"}
              aria-expanded={mobileOpen}
            >
              <span
                className={`w-5 h-[2px] bg-black transition-all duration-300 origin-center ${
                  mobileOpen ? "rotate-45 translate-y-[7px]" : ""
                }`}
              />
              <span
                className={`w-5 h-[2px] bg-black transition-all duration-300 ${
                  mobileOpen ? "opacity-0" : ""
                }`}
              />
              <span
                className={`w-5 h-[2px] bg-black transition-all duration-300 origin-center ${
                  mobileOpen ? "-rotate-45 -translate-y-[7px]" : ""
                }`}
              />
            </button>
          </div>
        </div>
      </header>

      {/* Mobile Drawer Overlay */}
      <div
        className={`fixed inset-0 z-40 flex flex-col justify-center items-start px-8 gap-6 md:hidden transition-all duration-300 ${
          isHome
            ? "bg-white/95 backdrop-blur-md"
            : "bg-[#f4f1ea]/98 backdrop-blur-md"
        } ${
          mobileOpen
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none"
        }`}
      >
        <button
          onClick={handleVerifyClick}
          className="w-full text-center rounded-2xl bg-[#FF5A1F] text-white py-3 text-[16px] font-medium shadow-md hover:bg-[#e04810] transition-colors"
        >
          + Paste Text or URL to Verify
        </button>

        <a
          href="#home"
          onClick={() => handleLinkClick("home")}
          className={`text-[26px] tracking-tight hover:opacity-60 transition-opacity ${
            currentView === "home" ? "font-semibold text-black" : "text-black/70"
          }`}
          style={{ fontFamily: "var(--font-heading)" }}
        >
          Home ✳︎
        </a>

        {NAV_LINKS.map((link) => {
          const active = currentView === link.id;
          return (
            <a
              key={link.id}
              href={link.href}
              onClick={() => handleLinkClick(link.id)}
              className={`text-[26px] tracking-tight transition-opacity ${
                active ? "font-semibold text-black" : "text-black/70 hover:opacity-60"
              }`}
              style={{ fontFamily: "var(--font-heading)" }}
            >
              {link.label}
            </a>
          );
        })}
      </div>
    </>
  );
}
