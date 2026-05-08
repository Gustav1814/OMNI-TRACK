/**
 * 3D tilt card — mouse-reactive perspective for next-gen feel
 */
import React, { useRef } from 'react';
import { motion, useMotionTemplate, useMotionValue, useSpring, useTransform } from 'framer-motion';

export default function TiltCard({ children, className = '', style = {}, intensity = 10, ...props }) {
  const ref = useRef(null);
  const rotateX = useSpring(0, { stiffness: 320, damping: 28 });
  const rotateY = useSpring(0, { stiffness: 320, damping: 28 });
  const scale = useSpring(1, { stiffness: 400, damping: 25 });
  const z = useSpring(0, { stiffness: 300, damping: 30 });

  const handleMove = (e) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const px = (e.clientX - cx) / (rect.width / 2);
    const py = (e.clientY - cy) / (rect.height / 2);
    rotateX.set(-py * intensity);
    rotateY.set(px * intensity);
    scale.set(1.02);
    z.set(12);
  };
  const handleLeave = () => {
    rotateX.set(0);
    rotateY.set(0);
    scale.set(1);
    z.set(0);
  };

  const transform = useMotionTemplate`perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(${scale}, ${scale}, ${scale}) translateZ(${z}px)`;
  const boxShadow = useTransform(
    z,
    (v) => {
      const lift = 12 + v * 2;
      const blur = 40 + v * 6;
      const glow = v > 0 ? `, 0 0 ${30 + v * 4}px -8px rgba(99, 102, 241, 0.15)` : '';
      return `0 ${Math.round(lift)}px ${Math.round(blur)}px -12px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.08)${glow}`;
    }
  );

  return (
    <motion.div
      ref={ref}
      className={`tilt-card ${className}`}
      style={{
        ...style,
        transform,
        boxShadow,
        transformStyle: 'preserve-3d',
        willChange: 'transform',
      }}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      {...props}
    >
      {children}
    </motion.div>
  );
}
