/**
 * OmniTrack AI — Premium custom cursor
 * Smooth follow, expand on interactive elements, cursor-movement pleasure.
 */

import React, { useState, useEffect, useRef } from 'react';
import { motion, useMotionValue, useSpring } from 'framer-motion';

const CursorDot = motion.div;
const CursorRing = motion.div;

export default function CustomCursor() {
  const [isHovering, setIsHovering] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const cursorX = useMotionValue(-100);
  const cursorY = useMotionValue(-100);

  const springConfig = { damping: 25, stiffness: 400 };
  const dotX = useSpring(cursorX, springConfig);
  const dotY = useSpring(cursorY, springConfig);
  const ringX = useSpring(cursorX, { damping: 20, stiffness: 200 });
  const ringY = useSpring(cursorY, { damping: 20, stiffness: 200 });

  const raf = useRef(null);

  useEffect(() => {
    const move = (e) => {
      if (raf.current) cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(() => {
        cursorX.set(e.clientX);
        cursorY.set(e.clientY);
      });
    };
    const enter = () => setIsVisible(true);
    const leave = () => setIsVisible(false);

    const hoverSelectors = 'a, button, [role="button"], input, select, textarea, .nav-item, .stat-card, .card, .btn';
    const onMouseOver = (e) => {
      if (e.target.closest(hoverSelectors)) setIsHovering(true);
    };
    const onMouseOut = (e) => {
      if (!e.relatedTarget?.closest?.(hoverSelectors)) setIsHovering(false);
    };

    window.addEventListener('mousemove', move);
    document.body.addEventListener('mouseenter', enter);
    document.body.addEventListener('mouseleave', leave);
    document.body.addEventListener('mouseover', onMouseOver);
    document.body.addEventListener('mouseout', onMouseOut);
    return () => {
      window.removeEventListener('mousemove', move);
      document.body.removeEventListener('mouseenter', enter);
      document.body.removeEventListener('mouseleave', leave);
      document.body.removeEventListener('mouseover', onMouseOver);
      document.body.removeEventListener('mouseout', onMouseOut);
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [cursorX, cursorY]);

  const isTouch = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches;
  useEffect(() => {
    if (!isTouch) document.body.classList.add('cursor-custom');
    return () => document.body.classList.remove('cursor-custom');
  }, [isTouch]);

  if (isTouch) return null;

  return (
    <>
      <CursorDot
        className="cursor-dot"
        style={{
          x: dotX,
          y: dotY,
          translateX: '-50%',
          translateY: '-50%',
          opacity: isVisible ? 1 : 0,
          scale: isHovering ? 0.5 : 1,
        }}
      />
      <CursorRing
        className="cursor-ring"
        style={{
          x: ringX,
          y: ringY,
          translateX: '-50%',
          translateY: '-50%',
          opacity: isVisible ? 1 : 0,
          scale: isHovering ? 1.8 : 1,
        }}
      />
    </>
  );
}
