import { useEffect, useRef } from "react";

interface UseScrubVideoOptions {
  sensitivity?: number;
  initialFraction?: number;
  smoothLerp?: boolean;
}

/**
 * High-performance, lag-free video scrubber.
 *
 * Key features for silky-smooth performance:
 * 1. Continuous animation clock: rAF runs constantly and is never canceled on mousemove.
 * 2. Exponential easing (lerp): Absorbs discrete mouse polling ticks into a glassy 60/120fps glide.
 * 3. Seek-pipeline queue: Only one seek in flight at a time to prevent hardware decoder stalls.
 * 4. Zero debounce latency: Mouse movements update target coordinates instantaneously.
 */
export function useScrubVideo({
  sensitivity = 0.8,
  initialFraction = 0.12,
  smoothLerp = true,
}: UseScrubVideoOptions = {}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const targetTimeRef = useRef<number>(0);
  const displayTimeRef = useRef<number>(0);
  const prevXRef = useRef<number | null>(null);
  const isSeekingRef = useRef<boolean>(false);
  const durationRef = useRef<number>(0);
  const rafIdRef = useRef<number>(0);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    // Set initial frame when video metadata loads
    const setupInitialFrame = () => {
      const dur = video.duration;
      if (dur && !Number.isNaN(dur) && dur > 0) {
        durationRef.current = dur;
        const initialTime = dur * initialFraction;
        targetTimeRef.current = initialTime;
        displayTimeRef.current = initialTime;
        try {
          video.currentTime = initialTime;
        } catch {
          // ignore potential premature seek errors
        }
      }
    };

    if (video.readyState >= 1) {
      setupInitialFrame();
    } else {
      video.addEventListener("loadedmetadata", setupInitialFrame, { once: true });
    }

    // Direct seeking function that safely sets video.currentTime
    const dispatchSeek = (time: number) => {
      const vid = videoRef.current;
      if (!vid || !durationRef.current) return;
      const clamped = Math.max(0, Math.min(durationRef.current, time));
      isSeekingRef.current = true;
      try {
        vid.currentTime = clamped;
      } catch {
        isSeekingRef.current = false;
      }
    };

    // When the browser finishes seeking to a frame
    const handleSeeked = () => {
      isSeekingRef.current = false;
      const vid = videoRef.current;
      if (!vid || !durationRef.current) return;

      const destination = smoothLerp ? displayTimeRef.current : targetTimeRef.current;
      const diff = Math.abs(vid.currentTime - destination);

      // If target drifted while previous seek was in-flight, immediately seek to latest target
      if (diff > 0.01) {
        dispatchSeek(destination);
      }
    };

    // Continuous smooth animation loop
    const animate = () => {
      const vid = videoRef.current;
      const dur = durationRef.current || (vid ? vid.duration : 0);

      if (vid && dur) {
        durationRef.current = dur;

        if (smoothLerp) {
          // Exponential smoothing (lerp) towards targetTime
          // 0.22 provides an immediate, responsive feel while eliminating micro-jitter
          const diff = targetTimeRef.current - displayTimeRef.current;
          if (Math.abs(diff) > 0.001) {
            displayTimeRef.current += diff * 0.22;
          } else {
            displayTimeRef.current = targetTimeRef.current;
          }
        } else {
          displayTimeRef.current = targetTimeRef.current;
        }

        // If not currently seeking and there is a noticeable offset, issue seek
        if (!isSeekingRef.current) {
          const seekDiff = Math.abs(vid.currentTime - displayTimeRef.current);
          if (seekDiff > 0.008) {
            dispatchSeek(displayTimeRef.current);
          }
        }
      }

      rafIdRef.current = requestAnimationFrame(animate);
    };

    // Start the continuous animation loop
    rafIdRef.current = requestAnimationFrame(animate);

    // Mouse scrub handler
    const handleMouseMove = (e: MouseEvent) => {
      const dur = durationRef.current || (videoRef.current ? videoRef.current.duration : 0);
      if (!dur) return;

      const currentX = e.clientX;
      if (prevXRef.current === null) {
        prevXRef.current = currentX;
        return;
      }

      const delta = currentX - prevXRef.current;
      prevXRef.current = currentX;

      // Ignore huge anomalous cursor teleportation jumps (e.g. alt-tab)
      if (Math.abs(delta) > window.innerWidth * 0.5) return;

      const offset = (delta / window.innerWidth) * sensitivity * dur;
      targetTimeRef.current = Math.max(0, Math.min(dur, targetTimeRef.current + offset));
    };

    const handleMouseLeave = () => {
      prevXRef.current = null;
    };

    // Touch support for mobile / tablet devices
    let touchStartX: number | null = null;
    const handleTouchMove = (e: TouchEvent) => {
      if (e.touches.length === 0) return;
      const dur = durationRef.current || (videoRef.current ? videoRef.current.duration : 0);
      if (!dur) return;

      const currentX = e.touches[0].clientX;
      if (touchStartX === null) {
        touchStartX = currentX;
        return;
      }

      const delta = currentX - touchStartX;
      touchStartX = currentX;

      const offset = (delta / window.innerWidth) * sensitivity * dur;
      targetTimeRef.current = Math.max(0, Math.min(dur, targetTimeRef.current + offset));
    };

    const handleTouchEnd = () => {
      touchStartX = null;
    };

    video.addEventListener("seeked", handleSeeked);
    window.addEventListener("mousemove", handleMouseMove, { passive: true });
    document.addEventListener("mouseleave", handleMouseLeave);
    window.addEventListener("touchmove", handleTouchMove, { passive: true });
    window.addEventListener("touchend", handleTouchEnd);

    return () => {
      cancelAnimationFrame(rafIdRef.current);
      video.removeEventListener("loadedmetadata", setupInitialFrame);
      video.removeEventListener("seeked", handleSeeked);
      window.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseleave", handleMouseLeave);
      window.removeEventListener("touchmove", handleTouchMove);
      window.removeEventListener("touchend", handleTouchEnd);
    };
  }, [sensitivity, initialFraction, smoothLerp]);

  return { videoRef };
}
