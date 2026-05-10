/**
 * OmniTrack AI — 3D dashboard background
 * Floating orb + subtle glow, reacts to mouse. Premium feel.
 */

import React, { useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Float, MeshTransmissionMaterial } from '@react-three/drei';

function Orb({ mouse }) {
  const mesh = useRef();
  useFrame((state) => {
    if (!mesh.current) return;
    const t = state.clock.elapsedTime * 0.12;
    mesh.current.position.x = (mouse.current?.x ?? 0) * 2.5;
    mesh.current.position.y = (mouse.current?.y ?? 0) * 2.5;
    mesh.current.rotation.x = t * 0.08;
    mesh.current.rotation.y = t * 0.1;
  });

  return (
    <Float speed={0.6} floatIntensity={0.4}>
      <mesh ref={mesh} scale={1.4}>
        <icosahedronGeometry args={[1, 1]} />
        <MeshTransmissionMaterial
          backside
          samples={4}
          thickness={0.4}
          chromaticAberration={0.08}
          anisotropy={0.25}
          distortion={0.15}
          distortionScale={0.15}
          temporalDistortion={0.08}
          iridescence={0.15}
          iridescenceIOR={1}
          iridescenceThicknessRange={[0, 1000]}
          color="#08080c"
          transmission={0.92}
          clearcoat={0.15}
          clearcoatRoughness={0.15}
          envMapIntensity={0.3}
        />
      </mesh>
    </Float>
  );
}

function Ring({ mouse }) {
  const mesh = useRef();
  useFrame((state) => {
    if (!mesh.current) return;
    const t = state.clock.elapsedTime * 0.08;
    mesh.current.position.x = (mouse.current?.x ?? 0) * 1.5;
    mesh.current.position.y = (mouse.current?.y ?? 0) * 1.5;
    mesh.current.rotation.z = t;
  });
  return (
    <mesh ref={mesh} position={[0, 0, -1]}>
      <torusGeometry args={[2.2, 0.02, 16, 64]} />
      <meshBasicMaterial color="#6366f1" transparent opacity={0.12} />
    </mesh>
  );
}

function SceneContent({ mouse }) {
  return (
    <>
      <color attach="background" args={['#030304']} />
      <fog attach="fog" args={['#030304', 8, 26]} />
      <ambientLight intensity={0.14} />
      <pointLight position={[8, 8, 8]} intensity={0.4} color="#1a1a2e" />
      <pointLight position={[-6, -6, 6]} intensity={0.22} color="#0f0f18" />
      <pointLight position={[0, 0, 10]} intensity={0.18} color="#16162a" />
      <Ring mouse={mouse} />
      <Orb mouse={mouse} />
    </>
  );
}

export default function Scene3D() {
  const mouse = useRef({ x: 0, y: 0 });

  return (
    <div
      className="scene3d-wrapper"
      onMouseMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        mouse.current = {
          x: (e.clientX - rect.left) / rect.width - 0.5,
          y: -(e.clientY - rect.top) / rect.height + 0.5,
        };
      }}
    >
      <Canvas
        camera={{ position: [0, 0, 8], fov: 50 }}
        dpr={[1, 1.5]}
        gl={{ alpha: true, antialias: true, powerPreference: 'high-performance' }}
      >
        <SceneContent mouse={mouse} />
      </Canvas>
    </div>
  );
}
