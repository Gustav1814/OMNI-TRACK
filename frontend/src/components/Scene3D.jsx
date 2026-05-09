/**
 * OmniTrack AI — 3D dashboard background
 * Lightweight version — simple rotating icosahedron with standard material.
 * GPU-friendly: no transmission material, limited DPR, frame loop on demand.
 */

import React, { useRef, useCallback, Suspense } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Float } from '@react-three/drei';

function Orb() {
  const mesh = useRef();
  useFrame((state) => {
    if (!mesh.current) return;
    const t = state.clock.elapsedTime * 0.1;
    mesh.current.rotation.x = t * 0.08;
    mesh.current.rotation.y = t * 0.1;
  });

  return (
    <Float speed={0.4} floatIntensity={0.3}>
      <mesh ref={mesh} scale={1.4}>
        <icosahedronGeometry args={[1, 1]} />
        <meshStandardMaterial
          color="#12121a"
          roughness={0.3}
          metalness={0.8}
          transparent
          opacity={0.7}
          envMapIntensity={0.2}
        />
      </mesh>
    </Float>
  );
}

function Ring() {
  const mesh = useRef();
  useFrame((state) => {
    if (!mesh.current) return;
    mesh.current.rotation.z = state.clock.elapsedTime * 0.06;
  });
  return (
    <mesh ref={mesh} position={[0, 0, -1]}>
      <torusGeometry args={[2.2, 0.02, 16, 48]} />
      <meshBasicMaterial color="#6366f1" transparent opacity={0.12} />
    </mesh>
  );
}

function SceneContent() {
  return (
    <>
      <color attach="background" args={['#030304']} />
      <fog attach="fog" args={['#030304', 8, 26]} />
      <ambientLight intensity={0.14} />
      <pointLight position={[8, 8, 8]} intensity={0.4} color="#1a1a2e" />
      <pointLight position={[-6, -6, 6]} intensity={0.22} color="#0f0f18" />
      <Ring />
      <Orb />
    </>
  );
}

export default function Scene3D() {
  return (
    <div className="scene3d-wrapper">
      <Canvas
        camera={{ position: [0, 0, 8], fov: 50 }}
        dpr={1}
        gl={{
          alpha: true,
          antialias: false,
          powerPreference: 'low-power',
          stencil: false,
          depth: false,
        }}
        frameloop="always"
        performance={{ min: 0.3 }}
      >
        <Suspense fallback={null}>
          <SceneContent />
        </Suspense>
      </Canvas>
    </div>
  );
}
