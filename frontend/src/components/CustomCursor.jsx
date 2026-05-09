/**
 * OmniTrack AI — Lightweight custom cursor
 * Uses CSS transforms instead of framer-motion springs for minimal overhead.
 * Throttled to ~30fps via requestAnimationFrame.
 */

import React, { useEffect, useRef, useState } from 'react';

export default function CustomCursor() {
  const dotRef = useRef(null);
  const ringRef = useRef(null);
  const pos = useRef({ x: -100, y: -100 });
  const ringPos = useRef({ x: -100, y: -100 });
  const [isHovering, setIsHovering] = useState(false);
  const rafRef = useRef(null);

  const isTouch = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches;

  useEffect(() => {
    if (isTouch) return;

    document.body.classList.add('cursor-custom');

    const onMove = (e) => {
      pos.current = { x: e.clientX, y: e.clientY };
    };

    const hoverSelectors = 'a, button, [role="button"], input, select, textarea, .nav-item, .stat-card, .card, .btn';
    const onOver = (e) => {
      if (e.target.closest(hoverSelectors)) setIsHovering(true);
    };
    const onOut = (e) => {
      if (!e.relatedTarget?.closest?.(hoverSelectors)) setIsHovering(false);
    };

    // Animate at ~30fps using rAF + time gating
    let lastFrame = 0;
    const animate = (time) => {
      rafRef.current = requestAnimationFrame(animate);
      if (time - lastFrame < 33) return; // ~30fps cap
      lastFrame = time;

      // Lerp ring position for smooth follow
      ringPos.current.x += (pos.current.x - ringPos.current.x) * 0.15;
      ringPos.current.y += (pos.current.y - ringPos.current.y) * 0.15;

      if (dotRef.current) {
        dotRef.current.style.transform = `translate(${pos.current.x - 4}px, ${pos.current.y - 4}px)`;
      }
      if (ringRef.current) {
        const size = isHovering ? 64 : 40;
        ringRef.current.style.transform = `translate(${ringPos.current.x - size / 2}px, ${ringPos.current.y - size / 2}px)`;
        ringRef.current.style.width = `${size}px`;
        ringRef.current.style.height = `${size}px`;
      }
    };
    rafRef.current = requestAnimationFrame(animate);

    window.addEventListener('mousemove', onMove, { passive: true });
    document.body.addEventListener('mouseover', onOver, { passive: true });
    document.body.addEventListener('mouseout', onOut, { passive: true });

    return () => {
      document.body.classList.remove('cursor-custom');
      window.removeEventListener('mousemove', onMove);
      document.body.removeEventListener('mouseover', onOver);
      document.body.removeEventListener('mouseout', onOut);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [isTouch, isHovering]);

  if (isTouch) return null;

  return (
    <>
      <div ref={dotRef} className="cursor-dot" style={{ opacity: 1 }} />
      <div ref={ringRef} className="cursor-ring" style={{ opacity: 1 }} />
    </>
  );
}
