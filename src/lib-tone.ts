import type { Tone } from "./types";

export const toneText: Record<Tone, string> = {
  support: "text-[#1f5740]",
  refute: "text-[#96301e]",
  neutral: "text-[#75560f]",
  quiet: "text-black/50",
};

export const toneBorder: Record<Tone, string> = {
  support: "border-[#1f5740]/35",
  refute: "border-[#96301e]/35",
  neutral: "border-[#75560f]/35",
  quiet: "border-black/15",
};
