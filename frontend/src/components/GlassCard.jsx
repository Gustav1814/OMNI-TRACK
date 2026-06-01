/**
 * OmniTrack AI — Glass Card Component
 * 3D tilt effect, gradient borders, hover animations
 * Works in both light and dark modes
 */

import React, { useState, useRef } from 'react';
import { motion } from 'framer-motion';

export default function GlassCard({
  children,
  className = '',
  accent = 'indigo',
  tilt = true,
  hoverScale = 1.02,
  glow = false,
  header,
  footer,
  padding = 'normal',
  ...props
}) {
  const [tiltStyle, setTiltStyle] = useState({ rotateX: 0, rotateY: 0 });
  const [isHovered, setIsHovered] = useState(false);
  const cardRef = useRef(null);

  const handleMouseMove = (e) => {
    if (!tilt || !cardRef.current) return;
    const rect = cardRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width - 0.5;
    const y = (e.clientY - rect.top) / rect.height - 0.5;
    setTiltStyle({
      rotateX: -y * 12,
      rotateY: x * 12,
    });
  };

  const handleMouseLeave = () => {
    setTiltStyle({ rotateX: 0, rotateY: 0 });
    setIsHovered(false);
  };

  const paddingClasses = {
    none: 'p-0',
    small: 'p-3',
    normal: 'p-5',
    large: 'p-6',
  };

  return (
    <motion.div
      ref={cardRef}
      className={`glass-card glass-card-${accent} ${className}`}
      onMouseMove={handleMouseMove}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={handleMouseLeave}
      animate={{
        rotateX: tiltStyle.rotateX,
        rotateY: tiltStyle.rotateY,
        scale: isHovered ? hoverScale : 1,
        z: isHovered ? 20 : 0,
      }}
      transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      style={{ transformStyle: 'preserve-3d' }}
      {...props}
    >
      {/* Gradient border effect */}
      <div className="glass-card-border" />
      
      {/* Glow effect on hover */}
      {glow && isHovered && (
        <motion.div
          className={`glass-card-glow glass-card-glow-${accent}`}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        />
      )}
      
      {/* Card content */}
      <div className={`glass-card-content ${paddingClasses[padding]}`}>
        {header && (
          <div className="glass-card-header">
            {header}
          </div>
        )}
        <div className="glass-card-body">
          {children}
        </div>
        {footer && (
          <div className="glass-card-footer">
            {footer}
          </div>
        )}
      </div>
    </motion.div>
  );
}

// Stat Card variant with icon, label, value
export function StatCard({
  icon: Icon,
  label,
  value,
  suffix,
  trend,
  accent = 'indigo',
  tag = 'Live',
  progress = 0,
}) {
  const [tilt, setTilt] = useState({ x: 0, y: 0 });

  const handleMouseMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width - 0.5) * 16;
    const y = ((e.clientY - rect.top) / rect.height - 0.5) * -12;
    setTilt({ x, y });
  };

  const rgbColors = {
    violet: '139, 92, 246',
    indigo: '99, 102, 241',
    cyan: '34, 211, 238',
    amber: '251, 191, 36',
    rose: '244, 63, 94',
    emerald: '16, 185, 129',
    sky: '56, 189, 248',
    gold: '212, 175, 55',
  };
  const rgb = rgbColors[accent] || rgbColors.indigo;

  return (
    <motion.div
      className={`stat-card-3d stat-card-3d-${accent}`}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setTilt({ x: 0, y: 0 })}
      whileHover={{ y: -4 }}
      transition={{ type: 'spring', stiffness: 400, damping: 25 }}
    >
      <div
        className="stat-card-3d-inner"
        style={{
          transform: `perspective(900px) rotateY(${tilt.x}deg) rotateX(${tilt.y}deg)`,
        }}
      >
        {/* Background gradient orb */}
        <div className="stat-card-orb" style={{ background: `radial-gradient(circle at 30% 30%, rgba(${rgb}, 0.4), transparent 70%)` }} />
        
        <div className="stat-card-top">
          <div className={`stat-icon-3d stat-icon-3d-${accent}`}>
            {Icon && <Icon size={20} />}
          </div>
          <span className={`stat-chip-3d stat-chip-3d-${accent}`}>{tag}</span>
        </div>
        
        <div className="stat-card-mid">
          <div className="stat-label-3d">{label}</div>
          <div className="stat-value-3d">
            {value}
            {suffix && <span className="stat-suffix-3d">{suffix}</span>}
          </div>
        </div>
        
        <div className="stat-card-foot">
          {trend !== undefined && (
            <div className={`stat-trend-pill-3d ${trend >= 0 ? 'up' : 'down'}`}>
              {trend >= 0 ? '↗' : '↘'} {Math.abs(trend)}%
            </div>
          )}
          {progress > 0 && (
            <div className="stat-progress-3d">
              <span
                className="stat-progress-bar"
                style={{
                  width: `${Math.max(8, Math.min(100, progress))}%`,
                  background: `linear-gradient(90deg, rgba(${rgb}, 0.95), rgba(${rgb}, 0.4))`,
                }}
              />
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// Section header component
export function PageHeader({ title, subtitle, children }) {
  return (
    <motion.div
      className="page-header-3d"
      initial={{ opacity: 0, y: -20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
    >
      <div className="page-header-content">
        <h1 className="page-title-3d">{title}</h1>
        {subtitle && <p className="page-subtitle-3d">{subtitle}</p>}
      </div>
      {children && <div className="page-header-actions">{children}</div>}
    </motion.div>
  );
}

// Info display row
export function InfoRow({ label, value, accent = 'indigo', icon: Icon }) {
  return (
    <div className={`info-row info-row-${accent}`}>
      <div className="info-row-label">
        {Icon && <Icon size={14} />}
        <span>{label}</span>
      </div>
      <div className="info-row-value">{value}</div>
    </div>
  );
}
