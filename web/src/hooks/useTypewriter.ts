import { useEffect, useRef, useState } from "react";

/**
 * «Печатная машинка»: показывает text постепенно, но не сбрасывается при
 * каждом новом SSE-чанке — продолжает с текущей позиции.
 */
export function useTypewriter(text: string, step = 2, intervalMs = 12): string {
  const [shown, setShown] = useState("");
  const indexRef = useRef(0);

  useEffect(() => {
    if (text.length < indexRef.current) {
      indexRef.current = 0;
      setShown("");
    }
    const timer = window.setInterval(() => {
      if (indexRef.current >= text.length) {
        window.clearInterval(timer);
        return;
      }
      indexRef.current = Math.min(text.length, indexRef.current + step);
      setShown(text.slice(0, indexRef.current));
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [text, step, intervalMs]);

  return shown;
}
