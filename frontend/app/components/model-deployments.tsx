"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, csrfJson } from "../../lib/api";

export type Model = { id: string; sha256: string; sizeBytes: number; createdAt: string; name: string; candidateMap: number | null; baselineMap: number | null };
export type Deployment = { id: string; modelId: string; deviceId: string; status: string; message: string; activeSha256: string | null; updatedAt: string };
export type Policy = { deviceId: string; name: string; modelName: string; activeSha256: string | null; online: boolean; mode: "AUTO" | "PINNED"; pinnedModelId: string | null; pinnedSha256: string | null; note: string; updatedBy: string | null; updatedAt: string | null };
export type Row = { policy: Policy; latestDeployment: Deployment | null; drift: boolean };
type History = { id: number; mode: string; pinnedModelId: string | null; note: string; changedBy: string; changedAt: string };
const names: Record<string, string> = { PENDING: "장치 요청 대기", DOWNLOADING: "다운로드 중", VALIDATING: "후보 검증 중", APPLYING: "적용 중", APPLIED: "적용 완료", FAILED: "실패", ROLLED_BACK: "이전 모델 복구", CANCELED: "취소됨" };
const cancelable = ["PENDING", "DOWNLOADING", "VALIDATING"];
const outcomes: Record<string, string> = { REQUESTED: "고정 모델 배포를 요청했습니다.", IN_PROGRESS: "같은 모델 배포가 이미 진행 중입니다.", ALREADY_ACTIVE: "장치가 이미 이 모델을 실행 중입니다.", NONE: "정책을 저장했습니다." };
const errors: Record<string, string> = { DEPLOYMENT_APPLYING: "장치가 모델을 적용하는 중입니다. 끝난 뒤 다시 바꾸세요.", MODEL_NOT_FOUND: "선택한 모델을 찾지 못했습니다.", DEVICE_PINNED: "고정된 장치에는 고정 모델만 배포할 수 있습니다.", DEPLOYMENT_ACTIVE: "이 장치에 진행 중인 배포가 있습니다.", DEPLOYMENT_NOT_CANCELABLE: "이미 적용 단계에 들어갔거나 끝난 배포는 취소할 수 없습니다.", MODEL_HASH_MISMATCH: "모델 파일 해시가 등록 정보와 다릅니다." };
const short = (sha: string | null | undefined) => sha ? sha.slice(0, 10) : "—";
const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "—";
const score = (m: Model) => m.candidateMap == null ? "" : ` · mAP50–95 ${m.candidateMap.toFixed(3)}`;

type Props = { onData?: (rows: Row[], models: Model[]) => void; reloadKey?: number; focus?: string | null };

/** 장치별 배포 정책 현황판. 고정(PINNED)이면 자동 파이프라인과 다른 모델 배포가 막힌다. */
export default function ModelDeployments({ onData, reloadKey = 0, focus = null }: Props = {}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ mode: "AUTO" | "PINNED"; modelId: string; note: string }>({ mode: "PINNED", modelId: "", note: "" });
  const [history, setHistory] = useState<History[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async (signal?: AbortSignal) => {
    const [r, m] = await Promise.all([api<Row[]>("/training/device-policies", { signal }), api<Model[]>("/training/models", { signal })]);
    setRows(r); setModels(m); onData?.(r, m);
  }, [onData]);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await load(controller.signal); }
      catch { if (!controller.signal.aborted) setError("배포 현황을 불러오지 못했습니다."); }
      finally { if (!controller.signal.aborted) timer = setTimeout(poll, 5000); }
    }
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [load, reloadKey]);
  const modelOf = (id: string | null) => models.find(m => m.id === id);
  const bySha = (sha: string | null) => models.find(m => m.sha256 === sha);
  const fail = (cause: unknown) => setError(cause instanceof ApiError ? errors[cause.message] ?? `요청을 처리할 수 없습니다 (${cause.message}).` : "서버에 연결하지 못했습니다.");

  async function open(row: Row) {
    setEditing(row.policy.deviceId); setError(""); setMessage("");
    setDraft({ mode: row.policy.mode, modelId: row.policy.pinnedModelId ?? "", note: "" });
    setHistory(await api<History[]>(`/training/device-policies/${row.policy.deviceId}/history`).catch(() => []));
  }
  async function save(device: string) {
    const target = modelOf(draft.modelId);
    const text = draft.mode === "AUTO" ? `${device}를 자동 배포로 바꿀까요? 평가를 통과한 새 모델이 자동으로 배포될 수 있습니다.`
      : target ? `${device}를 ${target.name}(SHA ${short(target.sha256)})로 고정할까요? 현재 모델과 다르면 바로 배포를 요청하고, 적용 전인 다른 배포는 취소합니다.`
      : `${device}를 현재 모델로 유지(고정)할까요? 적용 전인 배포는 취소합니다.`;
    if (!window.confirm(text)) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await csrfJson<{ deployment: string }>(`/training/device-policies/${device}`, { mode: draft.mode, modelId: draft.mode === "PINNED" && draft.modelId ? draft.modelId : null, note: draft.note });
      setMessage(`${device}: ${outcomes[result.deployment] ?? "저장했습니다."}`); setEditing(null); await load();
    } catch (cause) { fail(cause); } finally { setBusy(false); }
  }
  async function redeploy(row: Row) {
    if (!row.policy.pinnedModelId || !window.confirm(`${row.policy.deviceId}에 고정 모델을 다시 배포할까요?`)) return;
    setBusy(true); setError(""); setMessage("");
    try { await csrfJson("/training/deployments", { modelId: row.policy.pinnedModelId, deviceId: row.policy.deviceId }); setMessage(`${row.policy.deviceId}에 고정 모델 재배포를 요청했습니다.`); await load(); }
    catch (cause) { fail(cause); } finally { setBusy(false); }
  }
  async function cancel(item: Deployment) {
    if (!window.confirm(`${item.deviceId}에 대한 배포 요청을 취소할까요?`)) return;
    setBusy(true); setError(""); setMessage("");
    try { await csrfJson(`/training/deployments/${item.id}/cancel`, {}); setMessage(`${item.deviceId} 배포 요청을 취소했습니다.`); await load(); }
    catch (cause) { fail(cause); } finally { setBusy(false); }
  }

  const running = (p: Policy) => {
    const known = bySha(p.activeSha256);
    return <>{known ? <strong>{known.name}</strong> : <strong className="model-cell">{p.modelName || "모델 정보 없음"}</strong>}
      <span className="subline mono">{p.activeSha256 ? `SHA ${short(p.activeSha256)}` : "활성 해시 미보고"}</span></>;
  };
  const target = (p: Policy) => {
    if (p.mode === "AUTO") return <span className="muted">자동 (평가 통과 모델)</span>;
    const m = modelOf(p.pinnedModelId);
    return p.pinnedModelId ? <><strong>{m?.name ?? p.pinnedModelId.slice(0, 8)}</strong><span className="subline mono">SHA {short(p.pinnedSha256)}</span></> : <span className="muted">현재 모델 유지</span>;
  };
  return <div className="training-runs">
    <p>장치마다 배포 정책을 정합니다. <strong>고정</strong>이면 지정한 모델만 배포되고 자동 파이프라인의 배포와 다른 모델 배포 요청은 막힙니다. <strong>자동</strong>이면 평가를 통과한 새 모델이 배포될 수 있습니다.</p>
    <p className="muted">새 장치의 기본값은 고정(현재 모델 유지)입니다. 장치는 heartbeat(5초)로 배포 요청을 받아 적용합니다.</p>
    {error && <p role="alert">{error}</p>}{message && <p className="notice">{message}</p>}
    <div className="table-scroll"><table><thead><tr><th>장치</th><th>정책</th><th>지정 모델</th><th>실행 중 모델</th><th>최근 배포</th><th /></tr></thead><tbody>
      {rows.length === 0 && <tr><td colSpan={6}>등록된 장치가 없습니다.</td></tr>}
      {rows.map(row => { const p = row.policy, d = row.latestDeployment; return <tr key={p.deviceId} id={`device-${p.deviceId}`} className={focus === p.deviceId ? "row-focus" : undefined}>
        <td><strong>{p.name}</strong><span className="subline">{p.deviceId} · {p.online ? "온라인" : "오프라인"}</span></td>
        <td><span className={`badge ${p.mode === "PINNED" ? "neutral" : "success"}`}>{p.mode === "PINNED" ? "🔒 고정" : "자동"}</span>{p.updatedBy && <span className="subline">{p.updatedBy} · {time(p.updatedAt)}</span>}</td>
        <td>{target(p)}</td>
        <td>{running(p)}</td>
        <td>{d ? <>{names[d.status] ?? d.status}<span className="subline">{modelOf(d.modelId)?.name ?? d.modelId.slice(0, 8)} · {time(d.updatedAt)}</span>{d.message && <span className="subline">{d.message}</span>}</> : <span className="muted">없음</span>}</td>
        <td><div className="training-actions">
          <button onClick={() => void open(row)} disabled={busy}>정책 변경</button>
          {row.drift && <button onClick={() => void redeploy(row)} disabled={busy}>다시 배포</button>}
          {d && cancelable.includes(d.status) && <button onClick={() => void cancel(d)} disabled={busy}>배포 취소</button>}
        </div></td>
      </tr>; })}
    </tbody></table></div>
    {editing && <section className="training-run-card"><header><h3>{editing} 배포 정책</h3><button onClick={() => setEditing(null)}>닫기</button></header>
      <div className="deployment-controls">
        <label>정책 <select value={draft.mode} onChange={e => setDraft({ ...draft, mode: e.target.value as "AUTO" | "PINNED" })}><option value="PINNED">고정</option><option value="AUTO">자동</option></select></label>
        {draft.mode === "PINNED" && <label>고정 모델 <select value={draft.modelId} onChange={e => setDraft({ ...draft, modelId: e.target.value })}>
          <option value="">현재 모델 유지 (배포 안 함)</option>
          {models.map(m => <option key={m.id} value={m.id}>{m.name}{score(m)} · {time(m.createdAt)} · SHA {short(m.sha256)}</option>)}
        </select></label>}
        <label>메모 <input maxLength={200} value={draft.note} onChange={e => setDraft({ ...draft, note: e.target.value })} placeholder="변경 사유 (선택)" /></label>
        <button className="primary" disabled={busy} onClick={() => void save(editing)}>저장</button>
      </div>
      <h3>변경 이력</h3>{history.length === 0 ? <p className="muted">이력이 없습니다. 기본 정책(고정·현재 모델 유지)을 사용 중입니다.</p>
        : <ul>{history.map(h => <li key={h.id}>{time(h.changedAt)} · {h.changedBy} · {h.mode === "AUTO" ? "자동" : h.pinnedModelId ? `고정: ${modelOf(h.pinnedModelId)?.name ?? h.pinnedModelId.slice(0, 8)}` : "고정: 현재 모델 유지"}{h.note && ` · ${h.note}`}</li>)}</ul>}
    </section>}
  </div>;
}
