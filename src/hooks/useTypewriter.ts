import { useEffect, useRef, useState } from "react";

/**
 * Custom useTypewriter hook:
 * Takes text, speed (default 38ms per character), startDelay (default 600ms).
 * After the delay, reveals one character at a time.
 * Returns { displayed, done }.
 */
export function useTypewriter(text: string, speed = 38, startDelay = 600) {
  const [displayed, setDisplayed] = useState("");
  const [done, setDone] = useState(false);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    setDisplayed("");
    setDone(false);

    let timerId: ReturnType<typeof setTimeout>;

    timerId = setTimeout(() => {
      const startTime = performance.now();

      const tick = (now: number) => {
        const elapsed = now - startTime;
        const count = Math.min(text.length, Math.floor(elapsed / speed) + 1);
        setDisplayed(text.slice(0, count));

        if (count >= text.length) {
          setDone(true);
          return;
        }

        rafRef.current = requestAnimationFrame(tick);
      };

      rafRef.current = requestAnimationFrame(tick);
    }, startDelay);

    return () => {
      clearTimeout(timerId);
      cancelAnimationFrame(rafRef.current);
    };
  }, [text, speed, startDelay]);

  return { displayed, done };
}
