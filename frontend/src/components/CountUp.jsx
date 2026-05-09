/**
 * Animated count-up for stats — feels alive
 */
import React, { useEffect, useState } from 'react';
import { motion, useSpring } from 'framer-motion';

export default function CountUp({ value, duration = 0.8, suffix = '', prefix = '', format = (n) => n }) {
  const [display, setDisplay] = useState(0);
  const spring = useSpring(0, { stiffness: 75, damping: 20 });

  useEffect(() => {
    const num = typeof value === 'number' ? value : parseFloat(String(value).replace(/[^0-9.-]/g, '')) || 0;
    spring.set(num);
  }, [value, spring]);

  useEffect(() => {
    return spring.on('change', (v) => setDisplay(v));
  }, [spring]);

  const formatted = typeof value === 'string' && value.includes(',')
    ? value
    : `${prefix}${format(Math.round(display * 10) / 10)}${suffix}`;

  return <motion.span>{formatted}</motion.span>;
}
