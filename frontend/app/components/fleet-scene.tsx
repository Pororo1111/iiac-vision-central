"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";

import { stateColor, type FleetNode, type FleetState } from "./fleet-types";

/*
 * 영화 관제실(워게임의 NORAD 빅보드, 트론: 레거시의 그리드, 오블리비언의 밝은 관제 HUD)을 참고한 복셀 3D 배포 지도.
 * 시점 각도는 고정이고(회전 없음) 드래그로 이동, 휠·두 손가락으로 확대·축소한다.
 * 중앙 서버 안테나 고리에서 각 IoT 장치(엣지 박스·카메라·안테나)까지 연결선이 이어지고,
 * 장치는 heartbeat 점을 서버로 보내며 배포 패킷은 서버에서 장치로 간다. 오프라인 장치 연결은 점선이다.
 */
const CELL = 4;
const AZIMUTH = 0.5, ELEVATION = 0.6;
const glow = Object.fromEntries(Object.entries(stateColor).map(([k, v]) => [k, parseInt(v.slice(1), 16)])) as Record<FleetState, number>;
const CYAN = 0x0fa3b1, FLOOR = 0xe4ebef;

type Voxel = { p: [number, number, number]; s: [number, number, number]; c: number; k?: number; phase?: number };
type Fleet = {
  group: THREE.Group; points: THREE.Vector3[]; links: THREE.QuadraticBezierCurve3[];
  bodies: THREE.InstancedMesh; parts: THREE.InstancedMesh; beacons: THREE.InstancedMesh; pads: THREE.InstancedMesh; beams: THREE.InstancedMesh;
  hits: THREE.InstancedMesh; packets: THREE.InstancedMesh; pings: THREE.InstancedMesh; extent: { x: number; z: number };
};
const LINK_STEPS = 24;
/** 장치 안테나 끝(LED)의 장치 중심 기준 위치. 연결선이 여기서 끝난다. */
const LED = new THREE.Vector3(0.55, 2.65, -0.45);

function instanced(geometry: THREE.BufferGeometry, material: THREE.Material, list: Voxel[]) {
  const mesh = new THREE.InstancedMesh(geometry, material, Math.max(1, list.length)), o = new THREE.Object3D(), c = new THREE.Color();
  list.forEach((v, i) => { o.position.set(...v.p); o.scale.set(...v.s); o.updateMatrix(); mesh.setMatrixAt(i, o.matrix); mesh.setColorAt(i, c.setHex(v.c).multiplyScalar(v.k ?? 1)); });
  mesh.count = list.length;
  return mesh;
}
const glowMaterial = (additive = false) => new THREE.MeshBasicMaterial({ toneMapped: false, ...(additive ? { transparent: true, blending: THREE.AdditiveBlending, depthWrite: false } : {}) });

/** 중앙 서버 타워: 테두리가 빛나는 받침, 랙 LED가 깜빡이는 3×3 코어, 상단 캡과 안테나. */
function serverVoxels() {
  const solids: Voxel[] = [], lights: Voxel[] = [];
  for (let x = -3; x <= 3; x++) for (let z = -3; z <= 3; z++) {
    solids.push({ p: [x, 0.3, z], s: [0.96, 0.6, 0.96], c: 0xc3ced6 });
    if ((Math.abs(x) === 3 || Math.abs(z) === 3) && (x + z) % 2 === 0) lights.push({ p: [x, 0.63, z], s: [0.55, 0.05, 0.55], c: CYAN, k: 1, phase: (x * 7 + z * 3) * 0.3 });
  }
  for (let i = 0; i < 8; i++) for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++)
    if (Math.abs(x) === 1 || Math.abs(z) === 1) solids.push({ p: [x, 1.1 + i, z], s: [0.95, 0.95, 0.95], c: i % 3 === 2 ? 0xd5dde3 : 0xf2f5f7 });
  for (let i = 0; i < 8; i++) for (const [dx, dz] of [[0, 1], [0, -1], [1, 0], [-1, 0]]) {
    lights.push({ p: [dx * 1.49, 1.1 + i, dz * 1.49], s: [dz ? 0.42 : 0.04, 0.7, dx ? 0.42 : 0.04], c: CYAN, k: 1, phase: i * 0.7 + dx * 2 + dz });
    for (const o of [-1, 1]) lights.push({ p: [dx * 1.49 + dz * o, 1.1 + i + 0.2, dz * 1.49 + dx * o], s: [dz ? 0.16 : 0.04, 0.12, dx ? 0.16 : 0.04], c: (i + o + 3) % 3 ? 0x16a36a : 0xe8913a, k: 1.1, phase: Math.sin(i * 12.9 + o * 78.2 + dx * 3 + dz * 5) * 43758 });
  }
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) solids.push({ p: [x, 9.2, z], s: [0.97, 0.4, 0.97], c: 0xe9eef1 });
  for (let i = 0; i < 3; i++) solids.push({ p: [0, 9.9 + i * 0.8, 0], s: [0.3, 0.8, 0.3], c: 0x8d9aa4 });
  lights.push({ p: [0, 12.3, 0], s: [0.45, 0.45, 0.45], c: 0xe0444f, k: 1.4, phase: -1 });
  return { solids, lights };
}

/** 장치 칸을 서버 가까운 순으로 채운다. 화면이 가로로 넓어 x 방향으로 더 멀리 퍼진다. */
function layout(n: number) {
  const k = Math.ceil(Math.sqrt(n)) + 2, cells: { gx: number; gz: number }[] = [];
  for (let gz = -k; gz <= k; gz++) for (let gx = -2 * k; gx <= 2 * k; gx++) if (Math.abs(gx) > 1 || Math.abs(gz) > 1) cells.push({ gx, gz });
  cells.sort((a, b) => (a.gx ** 2 * 0.3 + a.gz ** 2) - (b.gx ** 2 * 0.3 + b.gz ** 2) || Math.atan2(a.gz, a.gx) - Math.atan2(b.gz, b.gx));
  return cells.slice(0, n).map(({ gx, gz }) => new THREE.Vector3(gx * CELL, 0, gz * CELL));
}

/** 서버 안테나 고리에서 장치 LED까지 위로 휘는 연결선. */
function link(p: THREE.Vector3) {
  const a = Math.atan2(p.z, p.x), start = new THREE.Vector3(Math.cos(a) * 4.4, 10.4, Math.sin(a) * 4.4), end = p.clone().add(LED);
  const control = start.clone().lerp(end, 0.5); control.y += 2 + start.distanceTo(end) * 0.12;
  return new THREE.QuadraticBezierCurve3(start, control, end);
}
function segments(curves: THREE.QuadraticBezierCurve3[]) {
  const out = new Float32Array(curves.length * LINK_STEPS * 6), a = new THREE.Vector3(), b = new THREE.Vector3();
  curves.forEach((curve, i) => { for (let j = 0; j < LINK_STEPS; j++) {
    curve.getPoint(j / LINK_STEPS, a); curve.getPoint((j + 1) / LINK_STEPS, b);
    out.set([a.x, a.y, a.z, b.x, b.y, b.z], (i * LINK_STEPS + j) * 6);
  } });
  return out;
}

function buildFleet(n: number, voxel: THREE.BufferGeometry, cube: THREE.BufferGeometry, metal: THREE.Material): Fleet {
  const group = new THREE.Group(), points = layout(n), links = points.map(link);
  const bodies = new THREE.InstancedMesh(voxel, metal, Math.max(1, n * 4)); bodies.castShadow = bodies.receiveShadow = true;
  const parts = new THREE.InstancedMesh(voxel, metal, Math.max(1, n * 3)); parts.castShadow = true;
  const beacons = new THREE.InstancedMesh(voxel, glowMaterial(), Math.max(1, n));
  const pads = new THREE.InstancedMesh(cube, glowMaterial(), Math.max(1, n));
  const beams = new THREE.InstancedMesh(cube, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.42, depthWrite: false, toneMapped: false }), Math.max(1, n));
  const hits = new THREE.InstancedMesh(cube, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }), Math.max(1, n));
  const packets = new THREE.InstancedMesh(cube, glowMaterial(), Math.max(1, n * 3));
  const pings = new THREE.InstancedMesh(cube, glowMaterial(), Math.max(1, n));
  const o = new THREE.Object3D(), c = new THREE.Color();
  points.forEach((p, i) => {
    o.position.set(p.x, 0.03, p.z); o.scale.set(2.9, 0.04, 2.9); o.updateMatrix(); pads.setMatrixAt(i, o.matrix);
    o.position.set(p.x, 1.3, p.z); o.scale.set(2.4, 2.8, 2.4); o.updateMatrix(); hits.setMatrixAt(i, o.matrix);
    [beacons, pads, beams].forEach(m => m.setColorAt(i, c.setScalar(0)));
    // 엣지 박스 위 카메라 몸체·렌즈, 안테나 기둥
    ([[[-0.3, 1.25, 0.1], [0.9, 0.55, 0.75], 0x46525e], [[-0.3, 1.25, 0.52], [0.34, 0.34, 0.12], 0x0e1a24], [[LED.x, 1.75, LED.z], [0.12, 1.5, 0.12], 0x8d9aa4]] as const)
      .forEach(([[dx, y, dz], [sx, sy, sz], color], j) => { o.position.set(p.x + dx, y, p.z + dz); o.scale.set(sx, sy, sz); o.updateMatrix(); parts.setMatrixAt(i * 3 + j, o.matrix); parts.setColorAt(i * 3 + j, c.setHex(color)); });
  });
  for (let i = 0; i < n * 4; i++) bodies.setColorAt(i, c.setHex(0xf1f4f6));
  // 장치가 0대여도 instanceColor 버퍼를 만들어 둔다(setColorAt이 최초 호출 때 할당).
  for (const m of [bodies, parts, beacons, pads, beams, packets, pings]) if (!m.instanceColor) m.setColorAt(0, c.setScalar(0));
  bodies.count = n * 4; parts.count = n * 3; beacons.count = pads.count = beams.count = hits.count = n; packets.count = pings.count = 0;
  group.add(pads, bodies, parts, beacons, beams, packets, pings, hits);
  const extent = { x: Math.max(12, ...points.map(p => Math.abs(p.x) + 3)), z: Math.max(8, ...points.map(p => Math.abs(p.z) + 3)) };
  return { group, points, links, bodies, parts, beacons, pads, beams, hits, packets, pings, extent };
}

/** 처음 시점(home)에서 장치 전체가 화면에 꽉 차도록 직교 투영 범위를 맞춘다. 이동·확대는 컨트롤이 그 위에 더한다. */
function fit(camera: THREE.OrthographicCamera, home: THREE.Camera, w: number, h: number, extent: { x: number; z: number }) {
  home.updateMatrixWorld();
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity; const v = new THREE.Vector3();
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) for (const y of [0, 16]) {
    v.set(sx * extent.x, y, sz * extent.z).applyMatrix4(home.matrixWorldInverse);
    x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x); y0 = Math.min(y0, v.y); y1 = Math.max(y1, v.y);
  }
  let fw = x1 - x0, fh = y1 - y0; const aspect = w / h;
  if (fw / fh > aspect) fh = fw / aspect; else fw = fh * aspect;
  fw *= 1.04; fh *= 1.04;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  Object.assign(camera, { left: cx - fw / 2, right: cx + fw / 2, top: cy + fh / 2, bottom: cy - fh / 2 });
  camera.updateProjectionMatrix();
}

/** 바닥 그리드: 칸 경계 굵은 선, 서버에서 퍼지는 동심원, 레이더 스윕. */
const gridShader = {
  vertexShader: "varying vec3 vPos; void main(){ vPos=(modelMatrix*vec4(position,1.)).xyz; gl_Position=projectionMatrix*viewMatrix*vec4(vPos,1.); }",
  fragmentShader: `uniform float uTime; uniform float uFade; varying vec3 vPos;
    float grid(vec2 p, float w){ vec2 d=abs(fract(p-.5)-.5)/fwidth(p); return 1.-min(min(d.x,d.y)/w,1.); }
    void main(){
      vec2 p=vPos.xz; float r=length(p)/uFade; float fade=1.-smoothstep(.3,1.,r);
      float lines=grid(p,1.)*.1+grid(p/${CELL}.+.5,1.3)*.34;
      float a=atan(p.y,p.x)/6.28318+.5; float s=fract(a-uTime*.07); float sweep=pow(1.-s,14.)*(1.-smoothstep(0.,1.,r))*.3;
      float ring=smoothstep(.02,0.,abs(fract(r*4.-uTime*.25)-.5)-.47)*.08*fade;
      gl_FragColor=vec4(vec3(.06,.52,.6),clamp((lines*fade+ring)*.9+sweep*.5,0.,1.));
    }`,
};

export default function FleetScene({ nodes, hovered, onHover, onSelect }: { nodes: FleetNode[]; hovered: number | null; onHover: (index: number | null) => void; onSelect: (id: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const live = useRef({ nodes, hovered, onHover, onSelect });
  const [failed, setFailed] = useState(false);
  const [clock, setClock] = useState("");
  const reset = useRef<() => void>(() => {});
  useLayoutEffect(() => { live.current = { nodes, hovered, onHover, onSelect }; }, [nodes, hovered, onHover, onSelect]);
  useEffect(() => { const tick = () => setClock(new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour12: false })); tick(); const id = setInterval(tick, 1000); return () => clearInterval(id); }, []);

  useEffect(() => {
    const el = box.current; if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" }); } catch { setFailed(true); return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.NeutralToneMapping; renderer.toneMappingExposure = 1;
    renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFShadowMap;
    el.prepend(renderer.domElement);
    renderer.domElement.setAttribute("role", "img");
    renderer.domElement.setAttribute("aria-label", "중앙 서버와 IoT 장치의 연결·배포 상태 관제 화면");

    const scene = new THREE.Scene(); scene.background = new THREE.Color(0xeef3f6);
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture; scene.environmentIntensity = 0.35;
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 800);
    camera.position.set(Math.sin(AZIMUTH) * Math.cos(ELEVATION), Math.sin(ELEVATION), Math.cos(AZIMUTH) * Math.cos(ELEVATION)).multiplyScalar(300);
    camera.lookAt(0, 0, 0);
    const home = camera.clone();
    const controls = new OrbitControls(camera, renderer.domElement);
    Object.assign(controls, { enableRotate: false, screenSpacePanning: false, zoomToCursor: true, enableDamping: true, dampingFactor: 0.12, minZoom: 0.7, maxZoom: 8 });
    controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_PAN };
    controls.saveState();
    reset.current = () => controls.reset();

    scene.add(new THREE.HemisphereLight(0xffffff, 0x9eabb4, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.5); key.position.set(-40, 70, 45); key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048); key.shadow.bias = -0.0004; key.shadow.normalBias = 0.03; scene.add(key, key.target);
    
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(600, 600), new THREE.MeshStandardMaterial({ color: FLOOR, roughness: 0.95, metalness: 0 }));
    floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
    const gridUniforms = { uTime: { value: 0 }, uFade: { value: 40 } };
    const grid = new THREE.Mesh(new THREE.PlaneGeometry(600, 600), new THREE.ShaderMaterial({ ...gridShader, uniforms: gridUniforms, transparent: true, depthWrite: false }));
    grid.rotation.x = -Math.PI / 2; grid.position.y = 0.012; scene.add(grid);

    const voxel = new RoundedBoxGeometry(1, 1, 1, 2, 0.08), cube = new THREE.BoxGeometry(1, 1, 1);
    const metal = new THREE.MeshStandardMaterial({ roughness: 0.5, metalness: 0.15 });
    const server = serverVoxels();
    const solids = instanced(voxel, metal, server.solids); solids.castShadow = solids.receiveShadow = true;
    const lights = instanced(cube, glowMaterial(), server.lights);
    const halo = new THREE.Group();
    for (let i = 0; i < 24; i++) {
      const m = new THREE.Mesh(cube, glowMaterial()), a = (i / 24) * Math.PI * 2;
      m.position.set(Math.cos(a) * 4.4, 10.4, Math.sin(a) * 4.4); m.scale.setScalar(i % 6 === 0 ? 0.42 : 0.24);
      m.material.color.setHex(CYAN).multiplyScalar(i % 6 === 0 ? 1.15 : 0.9); halo.add(m);
    }
    const pulse = new THREE.Mesh(new THREE.RingGeometry(0.97, 1, 128), new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, depthWrite: false, toneMapped: false }));
    pulse.rotation.x = -Math.PI / 2; pulse.position.y = 0.05;
    scene.add(solids, lights, halo, pulse);

    const target = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 });
    const composer = new EffectComposer(renderer, target);
    composer.addPass(new RenderPass(scene, camera)); composer.addPass(new OutputPass());

    const linkMaterial = new LineMaterial({ linewidth: 1.6, vertexColors: true, transparent: true, opacity: 0.8, depthWrite: false });
    const offlineMaterial = new LineMaterial({ color: 0x9aa7b0, linewidth: 1.2, dashed: true, dashSize: 0.45, gapSize: 0.35, transparent: true, opacity: 0.8, depthWrite: false });
    const focusMaterial = new LineMaterial({ color: CYAN, linewidth: 4, transparent: true, opacity: 0.95, depthWrite: false });
    const lines = [linkMaterial, offlineMaterial, focusMaterial].map(material => { const line = new LineSegments2(new LineSegmentsGeometry(), material); line.frustumCulled = false; scene.add(line); return line; });
    let linkNodes: FleetNode[] | null = null;
    /** 상태가 바뀐 목록을 받으면 연결선을 다시 만든다. 온라인은 상태 색 실선, 오프라인은 회색 점선이다. */
    const relink = (f: Fleet, nodes: FleetNode[]) => {
      linkNodes = nodes;
      const online = f.links.filter((_, i) => nodes[i].state !== "offline"), offline = f.links.filter((_, i) => nodes[i].state === "offline");
      const colors = new Float32Array(online.length * LINK_STEPS * 6); let k = 0;
      nodes.forEach(node => { if (node.state === "offline") return; tint.setHex(glow[node.state]).lerp(c.setHex(0xffffff), 0.25); for (let j = 0; j < LINK_STEPS * 2; j++) { colors.set([tint.r, tint.g, tint.b], k); k += 3; } });
      [online, offline].forEach((curves, i) => { lines[i].geometry.dispose(); const g = new LineSegmentsGeometry(); if (curves.length) g.setPositions(segments(curves)); lines[i].geometry = g; });
      if (online.length) lines[0].geometry.setColors(colors);
      if (offline.length) lines[1].computeLineDistances();
    };
    let fleet: Fleet | null = null, size = { w: 1, h: 1 }, lastHover: number | null = -1, raf = 0;
    const o = new THREE.Object3D(), c = new THREE.Color(), tint = new THREE.Color(), v = new THREE.Vector3(), timer = new THREE.Timer();
    const clamped = new THREE.Vector3();
    const resize = () => {
      size = { w: Math.max(1, el.clientWidth), h: Math.max(1, el.clientHeight) };
      renderer.setSize(size.w, size.h); composer.setPixelRatio(renderer.getPixelRatio()); composer.setSize(size.w, size.h);
      for (const line of lines) (line.material as LineMaterial).resolution.set(size.w, size.h);
      if (fleet) fit(camera, home, size.w, size.h, fleet.extent);
    };
    const disposeFleet = (f: Fleet) => {
      scene.remove(f.group);
      f.group.traverse(obj => { if (obj instanceof THREE.InstancedMesh) { obj.dispose(); if (obj.material !== metal) (obj.material as THREE.Material).dispose(); } });
    };
    const rebuild = (n: number) => {
      if (fleet) disposeFleet(fleet);
      fleet = buildFleet(n, voxel, cube, metal); scene.add(fleet.group); lastHover = -1;
      const r = Math.max(fleet.extent.x, fleet.extent.z);
      Object.assign(key.shadow.camera, { left: -r - 6, right: r + 6, top: r + 6, bottom: -r - 6, near: 1, far: 250 }); key.shadow.camera.updateProjectionMatrix();
      gridUniforms.uFade.value = r * 1.35;
      fit(camera, home, size.w, size.h, fleet.extent); controls.reset();
    };
    const observer = new ResizeObserver(resize); observer.observe(el); resize();

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame); timer.update(now);
      const t = timer.getElapsed(), { nodes, hovered } = live.current;
      if (!fleet || fleet.points.length !== nodes.length) rebuild(nodes.length);
      const f = fleet!;
      if (nodes !== linkNodes) relink(f, nodes);
      controls.update();
      // 이동은 장치가 있는 범위 안으로 제한한다.
      clamped.set(THREE.MathUtils.clamp(controls.target.x, -f.extent.x, f.extent.x), 0, THREE.MathUtils.clamp(controls.target.z, -f.extent.z, f.extent.z)).sub(controls.target);
      if (clamped.lengthSq() > 0) { controls.target.add(clamped); camera.position.add(clamped); }
      gridUniforms.uTime.value = t; halo.rotation.y = t * 0.35;
      const s = (t % 3.5) / 3.5;
      pulse.scale.setScalar(3.6 + s * Math.max(f.extent.x, f.extent.z)); (pulse.material as THREE.MeshBasicMaterial).opacity = (1 - s) ** 2 * 0.35;
      server.lights.forEach((l, i) => {
        const k = l.phase === -1 ? (Math.sin(t * 5) > 0.2 ? 1.3 : 0.5) : (l.k ?? 1) * (0.75 + 0.25 * Math.max(0, Math.sin(t * 2.2 + (l.phase ?? 0))));
        lights.setColorAt(i, c.setHex(l.c).multiplyScalar(k));
      });
      lights.instanceColor!.needsUpdate = true;

      if (hovered !== lastHover) {
        lastHover = hovered;
        f.points.forEach((p, i) => {
          [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]].forEach(([dx, dz], j) => { o.position.set(p.x + dx, 0.45, p.z + dz); o.scale.setScalar(0.92); o.updateMatrix(); f.bodies.setMatrixAt(i * 4 + j, o.matrix); });
          o.position.set(p.x + LED.x, LED.y, p.z + LED.z); o.scale.setScalar(i === hovered ? 0.62 : 0.46); o.updateMatrix(); f.beacons.setMatrixAt(i, o.matrix);
        });
        for (const m of [f.bodies, f.beacons]) { m.instanceMatrix.needsUpdate = true; m.computeBoundingSphere(); }
        const focus = hovered !== null ? f.links[hovered] : undefined;
        lines[2].geometry.dispose(); lines[2].geometry = new LineSegmentsGeometry(); if (focus) lines[2].geometry.setPositions(segments([focus]));
      }
      let packet = 0, ping = 0;
      nodes.forEach((node, i) => {
        const base = glow[node.state], wave = Math.sin(t * 3 + i * 0.7);
        const beacon = node.state === "offline" ? 0.8 : node.state === "deploying" ? (Math.sin(t * 8 + i) > 0 ? 1.25 : 0.75) : node.state === "failed" ? 1 + wave * 0.25 : 1;
        f.beacons.setColorAt(i, c.setHex(base).multiplyScalar(beacon));
        c.setHex(i === hovered ? 0xffffff : node.state === "offline" ? 0xb4bec5 : 0xe6ebee); for (let j = 0; j < 4; j++) f.bodies.setColorAt(i * 4 + j, c);
        f.pads.setColorAt(i, c.setHex(FLOOR).lerp(tint.setHex(i === hovered ? CYAN : base), i === hovered ? 0.6 : node.state === "offline" ? 0.12 : 0.3));
        const beam = node.state === "failed";
        o.position.set(f.points[i].x, 4.2, f.points[i].z); o.scale.set(beam ? 0.3 : 0, beam ? 6 : 0, beam ? 0.3 : 0); o.updateMatrix(); f.beams.setMatrixAt(i, o.matrix);
        f.beams.setColorAt(i, c.setHex(base).lerp(tint.setHex(0xffffff), node.state === "failed" ? 0.25 + 0.25 * wave : 0.2));
        // 온라인 장치는 5초마다 heartbeat 점을 서버로 보낸다.
        const beat = (t / 5 + i * 0.37) % 1;
        if (node.state !== "offline" && beat < 0.3) {
          f.links[i].getPoint(1 - beat / 0.3, v); o.position.copy(v); o.scale.setScalar(0.26); o.updateMatrix();
          f.pings.setMatrixAt(ping, o.matrix); f.pings.setColorAt(ping++, c.setHex(CYAN));
        }
        if (node.state !== "deploying") return;
        for (let j = 0; j < 3; j++) {
          f.links[i].getPoint((t * (node.applying ? 0.5 : 0.25) + j / 3 + i * 0.13) % 1, v);
          o.position.copy(v); o.scale.setScalar(j === 0 ? 0.5 : 0.36); o.updateMatrix();
          f.packets.setMatrixAt(packet, o.matrix); f.packets.setColorAt(packet++, c.setHex(glow.deploying).multiplyScalar(j === 0 ? 1.3 : 0.9));
        }
      });
      f.packets.count = packet; f.pings.count = ping;
      if (ping) { f.pings.instanceMatrix.needsUpdate = true; f.pings.instanceColor!.needsUpdate = true; f.pings.computeBoundingSphere(); }
      for (const m of [f.bodies, f.beacons, f.pads, f.beams]) m.instanceColor!.needsUpdate = true;
      f.beams.instanceMatrix.needsUpdate = true; f.beams.computeBoundingSphere();
      if (packet) { f.packets.instanceMatrix.needsUpdate = true; f.packets.instanceColor!.needsUpdate = true; f.packets.computeBoundingSphere(); }
      composer.render();
    };
    raf = requestAnimationFrame(frame);

    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(), canvas = renderer.domElement;
    const pick = (e: MouseEvent) => {
      if (!fleet) return null;
      const r = canvas.getBoundingClientRect(); ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      // 서버 타워에 가려진 장치는 고르지 않는다.
      const hit = ray.intersectObjects([fleet.hits, solids], false)[0];
      const i = hit?.object === fleet.hits ? hit.instanceId : undefined;
      return i === undefined || i >= live.current.nodes.length ? null : i;
    };
    let down: { x: number; y: number } | null = null;
    const press = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; canvas.style.cursor = "grabbing"; };
    const release = () => { canvas.style.cursor = "grab"; };
    const move = (e: PointerEvent) => {
      if (e.buttons) return;
      const i = pick(e); canvas.style.cursor = i === null ? "grab" : "pointer"; if (i !== live.current.hovered) live.current.onHover(i);
    };
    const leave = () => live.current.onHover(null);
    // 드래그로 이동한 뒤 손을 뗀 것은 선택으로 보지 않는다.
    const click = (e: MouseEvent) => { if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return; const i = pick(e); if (i !== null) live.current.onSelect(live.current.nodes[i].id); };
    canvas.style.cursor = "grab"; canvas.style.touchAction = "none";
    canvas.addEventListener("pointerdown", press); canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointermove", move); canvas.addEventListener("pointerleave", leave); canvas.addEventListener("click", click);

    return () => {
      cancelAnimationFrame(raf); observer.disconnect();
      controls.dispose(); canvas.removeEventListener("pointerdown", press); canvas.removeEventListener("pointerup", release);
      for (const line of lines) { line.geometry.dispose(); (line.material as LineMaterial).dispose(); scene.remove(line); }
      canvas.removeEventListener("pointermove", move); canvas.removeEventListener("pointerleave", leave); canvas.removeEventListener("click", click);
      if (fleet) disposeFleet(fleet);
      scene.traverse(obj => { if (obj instanceof THREE.Mesh) { obj.geometry.dispose(); (Array.isArray(obj.material) ? obj.material : [obj.material]).forEach(m => m.dispose()); } });
      voxel.dispose(); cube.dispose(); scene.environment?.dispose(); pmrem.dispose(); target.dispose(); composer.dispose(); renderer.dispose(); canvas.remove();
    };
  }, []);

  if (failed) return <div className="fleet-fallback">이 브라우저는 WebGL을 지원하지 않아 요약만 표시합니다.</div>;
  return <div ref={box} className="fleet-stage">
    <div className="fleet-hud" aria-hidden="true">
      <i className="hud-corner tl" /><i className="hud-corner tr" /><i className="hud-corner bl" /><i className="hud-corner br" />
      <div className="hud-title"><b>FLEET COMMAND</b><span>모델 배포 관제 · {nodes.length} UNITS</span></div>
      <div className="hud-clock"><i className="hud-live" />LIVE {clock} KST</div>
    </div>
    <div className="hud-help">드래그 이동 · 휠/두 손가락 확대·축소 <button type="button" onClick={() => reset.current()}>원래 보기</button></div>
  </div>;
}
