"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, csrfJson } from "../../lib/api";
import SideNav from "./side-nav";
import ModelDeployments, { type Model, type Row } from "./model-deployments";
import { stateColor, type FleetNode, type FleetState } from "./fleet-types";

const FleetScene = dynamic(() => import("./fleet-scene"), { ssr: false, loading: () => <div className="fleet-fallback">관제 화면을 불러오는 중…</div> });

type Source = "BASE" | "MANUAL" | "AUTO";
type CatalogModel = Model & { source: Source };
const tabs: { key: Source; title: string; hint: string }[] = [
  { key: "BASE", title: "베이스 모델", hint: "현장에 처음 배포한 기본 모델(ppe4). 문제가 생기면 이 모델로 되돌립니다." },
  { key: "MANUAL", title: "수동 파이프라인", hint: "관리자가 만든 파이프라인에서 평가를 통과한 모델입니다." },
  { key: "AUTO", title: "자동 파이프라인", hint: "매일 자동 파이프라인이 IoT 이미지로 학습해 평가를 통과한 모델입니다." },
];
const stateName: Record<FleetState, string> = { ok: "정상", deploying: "배포 중", failed: "최근 배포 실패", offline: "오프라인" };
const activeStatus = ["PENDING", "DOWNLOADING", "VALIDATING", "APPLYING"];
const outcomeName: Record<string, string> = { REQUESTED: "배포 요청", IN_PROGRESS: "이미 배포 중", ALREADY_ACTIVE: "이미 실행 중", NONE: "저장" };
const errorName: Record<string, string> = { DEPLOYMENT_APPLYING: "다른 모델 적용 중", MODEL_NOT_FOUND: "모델 없음", DEVICE_NOT_FOUND: "장치 없음", MODEL_HASH_MISMATCH: "모델 파일 해시 불일치" };
const date = (value: string) => new Date(value).toLocaleDateString("ko-KR", { year: "2-digit", month: "numeric", day: "numeric" });

function stateOf(row: Row): FleetState {
  const d = row.latestDeployment;
  if (!row.policy.online) return "offline";
  if (d && activeStatus.includes(d.status)) return "deploying";
  if (d && ["FAILED", "ROLLED_BACK"].includes(d.status)) return "failed";
  return "ok";
}

export default function ModelsPage() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [models, setModels] = useState<CatalogModel[]>([]);
  const [tab, setTab] = useState<Source>("BASE");
  const [hovered, setHovered] = useState<number | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [pinning, setPinning] = useState<CatalogModel | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [results, setResults] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => { api("/auth/me").catch(cause => { if (cause instanceof ApiError && cause.status === 401) router.replace("/login"); }); }, [router]);
  const onData = useCallback((r: Row[], m: Model[]) => { setRows(r); setModels(m as CatalogModel[]); }, []);

  const nodes: FleetNode[] = useMemo(() => rows.map(row => ({ id: row.policy.deviceId, state: stateOf(row), applying: row.latestDeployment?.status === "APPLYING" })), [rows]);
  const summary = useMemo(() => nodes.reduce<Record<FleetState, number>>((acc, n) => ({ ...acc, [n.state]: acc[n.state] + 1 }), { ok: 0, deploying: 0, failed: 0, offline: 0 }), [nodes]);
  const usage = (m: CatalogModel) => ({
    pinned: rows.filter(r => r.policy.pinnedModelId === m.id).length,
    running: rows.filter(r => r.policy.activeSha256 === m.sha256).length,
  });
  const hoveredRow = hovered !== null ? rows[hovered] : null;
  const select = (id: string) => { setFocus(id); document.getElementById(`device-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); };

  async function registerBase() {
    setBusy(true); setError("");
    try { await csrfJson("/training/models/base", {}); setReloadKey(k => k + 1); }
    catch (cause) { setError(cause instanceof ApiError && cause.message === "BASE_HASH_UNVERIFIED" ? "기본 모델 파일의 해시가 현장 보고값·평가 기준 모델과 달라 등록하지 않았습니다." : cause instanceof ApiError ? `기본 모델을 등록하지 못했습니다 (${cause.message}).` : "서버에 연결하지 못했습니다."); }
    finally { setBusy(false); }
  }
  function openPin(model: CatalogModel) { setPinning(model); setChosen([]); setNote(""); setResults([]); }
  async function pin() {
    if (!pinning || chosen.length === 0) return;
    if (!window.confirm(`${chosen.length}대를 ${pinning.name}(으)로 고정할까요? 실행 중 모델과 다르면 바로 배포하고, 적용 전인 다른 배포는 취소합니다. 자동 정책 장치도 고정으로 바뀝니다.`)) return;
    setBusy(true); const lines: string[] = [];
    for (const device of chosen) {
      try {
        const result = await csrfJson<{ deployment: string }>(`/training/device-policies/${device}`, { mode: "PINNED", modelId: pinning.id, note });
        lines.push(`${device}: ${outcomeName[result.deployment] ?? result.deployment}`);
      } catch (cause) { lines.push(`${device}: 실패 · ${cause instanceof ApiError ? errorName[cause.message] ?? cause.message : "연결 오류"}`); }
      setResults([...lines]);
    }
    setBusy(false); setReloadKey(k => k + 1);
  }

  const visible = models.filter(m => m.source === tab);
  return <div className="app-shell control-shell">
    <SideNav active="models" />
    <main className="training-main control-room">
      <header className="collection-heading"><div><p className="eyebrow">모델 운영</p><h1>모델 현황판</h1>
        <p className="muted">중앙 서버와 IoT 장치의 배포 상태를 보고, 장치마다 어떤 모델로 고정할지 정합니다. 장치는 5초 heartbeat로 배포를 받아 적용합니다.</p></div></header>
      {error && <p role="alert">{error}</p>}

      <section className="fleet-panel" aria-label="배포 관제 화면">
        <div className="fleet-canvas">
          <FleetScene nodes={nodes} hovered={hovered} onHover={setHovered} onSelect={select} />
          {hoveredRow && <div className="fleet-tooltip"><strong>{hoveredRow.policy.name}</strong><span>{hoveredRow.policy.deviceId}</span>
            <span>{stateName[stateOf(hoveredRow)]} · {hoveredRow.policy.mode === "PINNED" ? "고정" : "자동"}</span></div>}
          {rows.length === 0 && <div className="fleet-empty">등록된 장치가 없습니다.</div>}
        </div>
        <ul className="fleet-legend">
          <li><i style={{ background: "#0fa3b1" }} />중앙 서버</li>
          {(Object.keys(stateName) as FleetState[]).map(key => <li key={key}><i style={{ background: stateColor[key] }} />{stateName[key]} <b>{summary[key]}</b></li>)}
          <li className="muted">장치를 누르면 아래 표로 이동합니다. 연결선: 상태 색 실선(오프라인은 회색 점선) · 청록 점: heartbeat · 파란 패킷: 배포 전송 · 빛기둥: 배포 실패</li>
        </ul>
      </section>

      <section className="control-panel">
        <h2>배포 가능한 모델</h2>
        <div className="model-tabs" role="tablist">{tabs.map(t => <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "active" : undefined} onClick={() => setTab(t.key)}>
          {t.title} <b>{models.filter(m => m.source === t.key).length}</b></button>)}</div>
        <p className="muted">{tabs.find(t => t.key === tab)?.hint}</p>
        {tab === "BASE" && visible.length === 0 && <p>기본 모델이 아직 후보로 등록되지 않았습니다. <button disabled={busy} onClick={() => void registerBase()}>ppe4 기본 모델 등록</button></p>}
        {tab !== "BASE" && visible.length === 0 && <p className="muted">평가를 통과한 모델이 없습니다.</p>}
        <div className="model-grid">{visible.map(m => { const u = usage(m); return <article key={m.id} className="model-card">
          <h3>{m.name}</h3>
          <dl>
            <div><dt>mAP50–95</dt><dd>{m.candidateMap == null ? "—" : m.candidateMap.toFixed(3)}{m.baselineMap != null && m.candidateMap != null && <small> (기준 {m.baselineMap.toFixed(3)})</small>}</dd></div>
            <div><dt>등록</dt><dd>{date(m.createdAt)} · {(m.sizeBytes / 1048576).toFixed(1)} MB</dd></div>
            <div><dt>SHA</dt><dd className="mono">{m.sha256.slice(0, 12)}</dd></div>
            <div><dt>장치</dt><dd>고정 {u.pinned}대 · 실행 {u.running}대</dd></div>
          </dl>
          <button className="primary" disabled={busy || rows.length === 0} onClick={() => openPin(m)}>장치에 고정</button>
        </article>; })}</div>
      </section>

      <section className="control-panel"><h2>장치별 배포 정책</h2>
        <ModelDeployments onData={onData} reloadKey={reloadKey} focus={focus} /></section>

      {pinning && <div className="camera-backdrop" role="dialog" aria-modal="true" aria-label="모델 고정">
        <div className="pipeline-dialog"><h2>{pinning.name}(으)로 고정</h2><p className="muted mono">SHA {pinning.sha256.slice(0, 12)}</p>
          <label className="check-all"><input type="checkbox" checked={chosen.length === rows.length && rows.length > 0} onChange={e => setChosen(e.target.checked ? rows.map(r => r.policy.deviceId) : [])} /> 전체 선택</label>
          <ul className="device-picks">{rows.map(r => { const current = models.find(m => m.id === r.policy.pinnedModelId); return <li key={r.policy.deviceId}><label>
            <input type="checkbox" checked={chosen.includes(r.policy.deviceId)} onChange={e => setChosen(c => e.target.checked ? [...c, r.policy.deviceId] : c.filter(id => id !== r.policy.deviceId))} />
            <strong>{r.policy.name}</strong> <span className="muted">{r.policy.deviceId} · {r.policy.online ? "온라인" : "오프라인"} · {r.policy.mode === "AUTO" ? "자동" : current ? `고정: ${current.name}` : "고정: 현재 모델 유지"}</span></label></li>; })}</ul>
          <label>메모 <input maxLength={200} value={note} onChange={e => setNote(e.target.value)} placeholder="변경 사유 (선택)" /></label>
          {results.length > 0 && <ul className="pin-results">{results.map(line => <li key={line}>{line}</li>)}</ul>}
          <div className="training-actions"><button className="primary" disabled={busy || chosen.length === 0} onClick={() => void pin()}>{busy ? "처리 중…" : `${chosen.length}대 고정`}</button><button onClick={() => setPinning(null)}>닫기</button></div>
        </div></div>}
    </main>
  </div>;
}
