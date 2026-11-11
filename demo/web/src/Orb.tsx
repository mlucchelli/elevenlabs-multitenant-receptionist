import { useEffect, useRef } from "react";

export type OrbState = "idle" | "connecting" | "listening" | "speaking";

/**
 * The orb follows the real audio level: `level()` is read every frame and
 * written to a CSS variable, so React does not re-render at 60 fps.
 */
export function Orb({ state, level }: { state: OrbState; level: () => number }) {
  const ref = useRef<HTMLDivElement>(null);
  const read = useRef(level);
  useEffect(() => {
    read.current = level;
  }, [level]);

  useEffect(() => {
    let frame = 0;
    let smooth = 0;
    const tick = () => {
      smooth += (Math.min(1, read.current() * 1.7) - smooth) * 0.28;
      ref.current?.style.setProperty("--v", smooth.toFixed(3));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <div ref={ref} className="orb" data-state={state} aria-hidden="true">
      <span className="orb-ring" />
      <span className="orb-ring" />
      <span className="orb-body">
        <span className="orb-swirl" />
        <span className="orb-shine" />
      </span>
    </div>
  );
}
