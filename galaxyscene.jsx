/**
 * galaxyscene.jsx
 * -----------------
 * Univers 3D "constellation" de JARVIS : chaque document/outil Google est une planete,
 * coloree par categorie, reliee a ses voisins par des filaments lumineux. La camera
 * zoome automatiquement sur la planete concernee quand JARVIS utilise un outil, sans
 * jamais afficher de log texte.
 *
 * PROPS
 *   nodes             : [{ id, name, mime_type, url }]        -> depuis GET /api/galaxy
 *   links              : [{ source, target }]                  -> depuis GET /api/galaxy
 *   sourceNodeIds      : string[]  -> ChatResponse.source_node_ids (fichiers precis touches)
 *   activeCategories   : string[]  -> ChatResponse.active_categories (drive/docs/sheets/gmail/prediction/search)
 *   jarvisState        : 'idle' | 'listening' | 'thinking' | 'speaking'
 */

import React, { useRef, useMemo, useEffect, useState } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Line } from '@react-three/drei';
import * as THREE from 'three';

// ----------------------------------------------------------------------------
// Categories & couleurs (cf. cahier des charges)
// ----------------------------------------------------------------------------

export const CATEGORY_COLORS = {
  docs: '#22e8ff',        // Google Docs / Notes -> Cyan
  sheets: '#34d399',      // Google Sheets / Donnees -> Vert
  gmail: '#fb7185',       // Gmail / Emails -> Rouge / Rose
  prediction: '#fbbf24',  // Script prediction football & IA -> Jaune / Or
  drive: '#c4b5fd',       // Dossiers Drive & fichiers systeme -> Violet / Blanc
  search: '#e5e7eb',      // Recherche web (systeme) -> Blanc casse
};

const CLUSTER_ORDER = ['docs', 'sheets', 'gmail', 'prediction', 'drive', 'search'];

function getNodeCategory(node) {
  if (!node) return 'drive';
  if (node.id && node.id.startsWith('system:')) {
    const suffix = (node.mime_type || '').split('/')[1];
    return CATEGORY_COLORS[suffix] ? suffix : 'drive';
  }
  const mime = node.mime_type || '';
  if (mime.includes('document')) return 'docs';
  if (mime.includes('spreadsheet')) return 'sheets';
  return 'drive'; // dossiers, fichiers divers, systeme -> violet/blanc
}

// ----------------------------------------------------------------------------
// Layout deterministe : cluster radial par categorie + spirale doree par noeud,
// pas de simulation physique (pas de dependance supplementaire), stable entre renders.
// ----------------------------------------------------------------------------

function hashSeed(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) { h = (h * 31 + str.charCodeAt(i)) >>> 0; }
  return h;
}

function computeLayout(nodes) {
  const byCategory = {};
  nodes.forEach((n) => {
    const cat = getNodeCategory(n);
    (byCategory[cat] = byCategory[cat] || []).push(n);
  });

  const positions = {};
  const clusterRadius = 13;
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));

  CLUSTER_ORDER.forEach((cat, ci) => {
    const items = byCategory[cat] || [];
    const clusterAngle = (ci / CLUSTER_ORDER.length) * Math.PI * 2;
    const clusterCenter = new THREE.Vector3(
      Math.cos(clusterAngle) * clusterRadius,
      0,
      Math.sin(clusterAngle) * clusterRadius
    );

    items.forEach((node, i) => {
      const seed = hashSeed(node.id);
      const spiralR = 1.2 + Math.sqrt(i + 1) * 1.15;
      const angle = i * goldenAngle + (seed % 1000) / 1000;
      const yJitter = (((seed >> 8) % 200) / 100 - 1) * 1.6;

      positions[node.id] = new THREE.Vector3(
        clusterCenter.x + Math.cos(angle) * spiralR,
        yJitter,
        clusterCenter.z + Math.sin(angle) * spiralR
      );
    });
  });

  return positions;
}

// ----------------------------------------------------------------------------
// Une planete
// ----------------------------------------------------------------------------

function Planet({ node, position, isActive, jarvisState }) {
  const meshRef = useRef();
  const category = getNodeCategory(node);
  const baseColor = CATEGORY_COLORS[category] || CATEGORY_COLORS.drive;
  const isSystemNode = node.id.startsWith('system:');
  const baseRadius = isSystemNode ? 0.85 : node.mime_type === 'application/vnd.google-apps.folder' ? 0.75 : 0.55;

  useFrame(({ clock }) => {
    if (!meshRef.current) return;
    const t = clock.getElapsedTime();
    if (isActive) {
      const speed = jarvisState === 'thinking' ? 5 : 3;
      const pulse = 1 + Math.sin(t * speed) * 0.22;
      meshRef.current.scale.setScalar(pulse);
      meshRef.current.material.emissiveIntensity = 1.4 + Math.sin(t * speed) * 0.6;
    } else {
      meshRef.current.scale.setScalar(1);
      meshRef.current.material.emissiveIntensity = 0.35;
    }
  });

  return (
    <group position={position}>
      <mesh ref={meshRef}>
        <sphereGeometry args={[baseRadius, 24, 24]} />
        <meshStandardMaterial
          color={baseColor}
          emissive={baseColor}
          emissiveIntensity={0.35}
          roughness={0.35}
          metalness={0.15}
        />
      </mesh>
      {isActive && (
        <mesh>
          <sphereGeometry args={[baseRadius * 1.6, 16, 16]} />
          <meshBasicMaterial color={baseColor} transparent opacity={0.12} />
        </mesh>
      )}
    </group>
  );
}

// ----------------------------------------------------------------------------
// Filaments lumineux entre noeuds lies
// ----------------------------------------------------------------------------

function ConstellationLinks({ links, positions }) {
  return (
    <>
      {links.map((link, i) => {
        const from = positions[link.source];
        const to = positions[link.target];
        if (!from || !to) return null;
        return (
          <Line
            key={`${link.source}-${link.target}-${i}`}
            points={[from, to]}
            color="#22e8ff"
            transparent
            opacity={0.18}
            lineWidth={1}
          />
        );
      })}
    </>
  );
}

// ----------------------------------------------------------------------------
// Coeur central (reacteur) : pulsation globale refletant l'etat de JARVIS
// ----------------------------------------------------------------------------

const STATE_PULSE = {
  idle: { color: '#22e8ff', speed: 1.2, intensity: 0.6 },
  listening: { color: '#fbbf24', speed: 2.2, intensity: 1.0 },
  thinking: { color: '#22e8ff', speed: 4.5, intensity: 1.4 },
  speaking: { color: '#fbbf24', speed: 3, intensity: 1.2 },
};

function ReactorCore({ jarvisState }) {
  const coreRef = useRef();
  const haloRef = useRef();
  const cfg = STATE_PULSE[jarvisState] || STATE_PULSE.idle;

  useFrame(({ clock }) => {
    const t = clock.getElapsedTime();
    const pulse = 1 + Math.sin(t * cfg.speed) * 0.18;
    if (coreRef.current) {
      coreRef.current.scale.setScalar(pulse);
      coreRef.current.material.emissiveIntensity = cfg.intensity + Math.sin(t * cfg.speed) * 0.4;
    }
    if (haloRef.current) {
      haloRef.current.scale.setScalar(pulse * 1.8);
      haloRef.current.material.opacity = 0.08 + Math.abs(Math.sin(t * cfg.speed)) * 0.06;
    }
  });

  return (
    <group>
      <mesh ref={coreRef}>
        <sphereGeometry args={[1.1, 32, 32]} />
        <meshStandardMaterial color={cfg.color} emissive={cfg.color} emissiveIntensity={0.6} />
      </mesh>
      <mesh ref={haloRef}>
        <sphereGeometry args={[1.1, 16, 16]} />
        <meshBasicMaterial color={cfg.color} transparent opacity={0.1} />
      </mesh>
      <pointLight color={cfg.color} intensity={2} distance={20} />
    </group>
  );
}

// ----------------------------------------------------------------------------
// Camera dynamique : zoom fluide sur la planete active, remplace tout log texte
// ----------------------------------------------------------------------------

function CameraRig({ focusPosition, jarvisState }) {
  const { camera } = useThree();
  const controlsRef = useRef();
  const groupRotationRef = useRef(0);

  useFrame((_, delta) => {
    const controls = controlsRef.current;
    if (!controls) return;

    if (focusPosition) {
      // Vise la planete active : la camera se rapproche en gardant un angle de survol.
      const offset = new THREE.Vector3(4.5, 3, 4.5);
      const desiredCamPos = focusPosition.clone().add(offset);
      camera.position.lerp(desiredCamPos, 0.045);
      controls.target.lerp(focusPosition, 0.06);
    } else if (jarvisState === 'idle') {
      // Rotation lente et autonome de la scene au repos, pour une galaxie "vivante".
      groupRotationRef.current += delta * 0.015;
      const radius = 22;
      const idlePos = new THREE.Vector3(
        Math.sin(groupRotationRef.current) * radius,
        6,
        Math.cos(groupRotationRef.current) * radius
      );
      camera.position.lerp(idlePos, 0.01);
      controls.target.lerp(new THREE.Vector3(0, 0, 0), 0.02);
    }
    controls.update();
  });

  return (
    <OrbitControls
      ref={controlsRef}
      enableDamping
      dampingFactor={0.08}
      minDistance={4}
      maxDistance={45}
      touches={{ ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN }}
    />
  );
}

// ----------------------------------------------------------------------------
// Composant principal
// ----------------------------------------------------------------------------

export default function GalaxyScene({ nodes = [], links = [], sourceNodeIds = [], activeCategories = [], jarvisState = 'idle' }) {
  const positions = useMemo(() => computeLayout(nodes), [nodes]);

  const activeNodeIds = useMemo(() => {
    const ids = new Set(sourceNodeIds);
    if (activeCategories.length) {
      nodes.forEach((n) => {
        if (activeCategories.includes(getNodeCategory(n)) && n.id.startsWith('system:')) {
          ids.add(n.id); // planete systeme representative de la categorie active
        }
      });
    }
    return ids;
  }, [nodes, sourceNodeIds, activeCategories]);

  const focusPosition = useMemo(() => {
    const firstActiveId = [...activeNodeIds][0];
    return firstActiveId && positions[firstActiveId] ? positions[firstActiveId] : null;
  }, [activeNodeIds, positions]);

  return (
    <>
      <ambientLight intensity={0.25} />
      <hemisphereLight args={['#0d1a2b', '#020608', 0.4]} />

      <ReactorCore jarvisState={jarvisState} />
      <ConstellationLinks links={links} positions={positions} />

      {nodes.map((node) => {
        const pos = positions[node.id];
        if (!pos) return null;
        return (
          <Planet
            key={node.id}
            node={node}
            position={pos}
            isActive={activeNodeIds.has(node.id)}
            jarvisState={jarvisState}
          />
        );
      })}

      <CameraRig focusPosition={focusPosition} jarvisState={jarvisState} />
    </>
  );
}
