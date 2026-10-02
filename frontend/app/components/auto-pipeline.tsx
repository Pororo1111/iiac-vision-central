"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, csrfJson } from "../../lib/api";

type Settings = { enabled: boolean; runAt: string; minSamples: number; epochs: number; basePolicy: "LATEST" | "DEFAULT"; lastRunDate: string | null; lastResult: string | null };
type Status = { settings: Settings; pending: number; labelingQueued: number; discardedToday: number; devices: number; onlineDevices: number; nextRunAt: string; latestPipelineId: string | null };
const errors: Record<string, string> = { NO_NEW_IMAGES: "학습에 쓸 새 IoT 이미지가 없습니다.", TRAINING_BUSY: "다른 학습이나 파이프라인이 진행 중입니다. 끝난 뒤 다시 실행하세요." };

/** 여러 IoT가 보낸 이미지로 매일 정해진 시각에 도는 전역 자동 파이프라인의 흐름·상태·설정. */
export default function AutoPipeline({ onStarted }: { onStarted: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const data = await api<Status>("/training/auto-pipeline", { signal });
    if (!signal?.aborted) setStatus(data);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(controller.signal); } catch { /* 목록 화면의 오류 표시를 따른다. */ }
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);
  async function save(next: Settings) {
    setError(""); setMessage("");
    try {
      const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
      const data = await api<Status>("/training/auto-pipeline", { method: "PUT", headers: { [csrf.headerName]: csrf.token, "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next.enabled, runAt: next.runAt.slice(0, 5), minSamples: next.minSamples, epochs: next.epochs, basePolicy: next.basePolicy }) });
      setStatus(data); setEditing(false); setMessage("자동 파이프라인 설정을 저장했습니다.");
    } catch (cause) { setError(cause instanceof ApiError ? `설정을 저장하지 못했습니다 (${cause.message}).` : "서버에 연결하지 못했습니다."); }
  }
  async function runNow() {
    setError(""); setMessage("");
    try { await csrfJson("/training/auto-pipeline/run", {}); setMessage("자동 파이프라인을 지금 실행했습니다. 아래 목록에서 진행 상황을 확인하세요."); onStarted(); await refresh(); }
    catch (cause) { setError(cause instanceof ApiError ? errors[cause.message] ?? `실행하지 못했습니다 (${cause.message}).` : "서버에 연결하지 못했습니다."); }
  }
  if (!status) return <section className="auto-pipeline"><p className="muted">자동 파이프라인 상태를 불러오는 중입니다.</p></section>;
  const s = status.settings;
  const percent = Math.min(100, status.pending / s.minSamples * 100);
  const next = new Date(status.nextRunAt).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const nodes = [
    { title: "IoT 수집", value: `${status.onlineDevices}/${status.devices}대 연결`, hint: "사람 감지 시 전송" },
    { title: "Gemma4 라벨링", value: `대기 ${status.labelingQueued}장`, hint: `빈 이미지 오늘 ${status.discardedToday}장 삭제` },
    { title: "학습 대기", value: `${status.pending} / ${s.minSamples}장`, hint: "태그 있는 새 이미지" },
    { title: "데이터셋 · 학습", value: `매일 ${s.runAt.slice(0, 5)}`, hint: `${s.epochs} epoch · ${s.basePolicy === "LATEST" ? "최근 통과 모델 이어학습" : "현장 기본 모델"}` },
  ];
  return <section className={`auto-pipeline ${s.enabled ? "on" : "off"}`} aria-label="자동 파이프라인">
    <header className="collection-heading">
      <div><p className="eyebrow">상시 운영</p><h2>자동 파이프라인</h2>
        <p className="muted">{s.enabled ? `다음 실행 ${next} · 새 이미지가 ${s.minSamples}장 이상일 때만 학습합니다.` : "꺼져 있습니다. 켜면 매일 정해진 시각에 실행합니다."}</p></div>
      <div className="training-actions">
        <label className="switch"><input type="checkbox" checked={s.enabled} onChange={e => void save({ ...s, enabled: e.target.checked })} /><span>{s.enabled ? "켜짐" : "꺼짐"}</span></label>
        <button onClick={() => { setDraft(s); setEditing(!editing); }}>{editing ? "설정 닫기" : "설정"}</button>
        <button className="primary" disabled={status.pending < 1} onClick={() => void runNow()}>지금 실행</button>
      </div>
    </header>
    {error && <p className="training-error" role="alert">{error}</p>}{message && <p className="training-success" role="status">{message}</p>}
    <ol className="auto-flow">{nodes.map((node, index) => <li key={node.title} className={s.enabled ? "live" : ""} style={{ ["--i" as string]: index }}>
      <span className="auto-flow-title">{node.title}</span><strong>{node.value}</strong><small>{node.hint}</small>
      {index === 2 && <span className="auto-gauge" role="progressbar" aria-valuemin={0} aria-valuemax={s.minSamples} aria-valuenow={status.pending}><span style={{ width: `${percent}%` }} /></span>}
    </li>)}</ol>
    {s.lastResult && <p className="muted">최근 트리거: {s.lastRunDate} · {s.lastResult}</p>}
    {editing && draft && <div className="auto-settings">
      <label>실행 시각(한국 시간) <input type="time" value={draft.runAt.slice(0, 5)} onChange={e => setDraft({ ...draft, runAt: e.target.value })} /></label>
      <label>학습 시작 최소 장수 <input type="number" min={1} max={1000} value={draft.minSamples} onChange={e => setDraft({ ...draft, minSamples: Number(e.target.value) })} /></label>
      <label>Epoch <input type="number" min={1} max={100} value={draft.epochs} onChange={e => setDraft({ ...draft, epochs: Number(e.target.value) })} /></label>
      <label>베이스 모델 <select value={draft.basePolicy} onChange={e => setDraft({ ...draft, basePolicy: e.target.value as Settings["basePolicy"] })}><option value="LATEST">최근 통과 모델에서 이어학습</option><option value="DEFAULT">현장 기본 모델</option></select></label>
      <button className="primary" onClick={() => void save(draft)}>저장</button>
    </div>}
  </section>;
}
