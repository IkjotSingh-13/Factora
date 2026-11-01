import { useEffect, useRef } from "react";

/**
 * Cursor parallax for the hero video.
 *
 * The old version sought the video (`video.currentTime = ...`) on every
 * mousemove — each seek stalls the decoder and forces everything painted on
 * top of the video (notably the frosted navbar) to re-raster, which is what
 * made the page feel laggy.
 *
 * This version never touches the decoder: it only moves the element with a
 * compositor-friendly `transform: translate3d(...)`, smoothed by an
 * exponential lerp on a rAF clock. The element is pre-scaled so the drift
 * never reveals an edge.
 */
export function useParallax(strength = 0.04) {
  const ref = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let targetX = 0;
    let targetY = 0;
    let curX = 0;
    let curY = 0;
    let raf = 0;

    const onMove = (event: MouseEvent) => {
      // normalised to -1..1
      targetX = (event.clientX / window.innerWidth - 0.5) * 2;
      targetY = (event.clientY / window.innerHeight - 0.5) * 2;
    };

    const tick = () => {
      curX += (targetX - curX) * 0.055;
      curY += (targetY - curY) * 0.055;
      el.style.transform = `translate3d(${(-curX * strength * 100).toFixed(3)}%, ${(
        -curY * strength * 60
      ).toFixed(3)}%, 0) scale(1.1)`;
      raf = requestAnimationFrame(tick);
    };

    window.addEventListener("mousemove", onMove, { passive: true });
    raf = requestAnimationFrame(tick);
    return () => {
      window.removeEventListener("mousemove", onMove);
      cancelAnimationFrame(raf);
    };
  }, [strength]);

  return ref;
}
