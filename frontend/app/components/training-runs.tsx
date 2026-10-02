"use client";

import { useEffect, useState } from "react";
import { api, csrfJson } from "../../lib/api";

type Sample = { id: string; title: string; deviceId: string };
type Run = { id: string; name: string; state: string; summary: string; automation: string | null; createdAt: string };
type Device = { deviceId: string; name: string };
type Summary = { selected?: number; excluded?: number; error?: string; modelSha256?: string; evaluation?: { eligibleForPromotion: boolean; deltaMap50_95: number }; selection?: { sampleId: string; include: boolean; reason: string }[]; automation?: { release?: string; deployment?: string; reason?: string } };
const automationText: Record<string, string> = { REGISTERED: "후보 등록 완료", REQUESTED: "배포 요청 완료", SKIPPED_NO_DEVICE: "배포 장치 미지정", STOPPED: "중단" };
const states: Record<string, string> = { PREPARING: "박스 생성·선별 중", DATASET_READY: "데이터셋 준비 완료", TRAINING: "학습 중", TRAINED: "후보 모델 생성 완료", EVALUATING: "평가 중", EVALUATED: "평가 완료", FAILED: "실패" };
export default function TrainingRuns({ step }: { step: string }) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [epochs, setEpochs] = useState(5);
  const [auto, setAuto] = useState(true);
  const [autoEpochs, setAutoEpochs] = useState(1);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const [data, history] = await Promise.all([api<{ items: Sample[] }>("/training/samples?status=LABELED", { signal: controller.signal }), api<Run[]>("/training/runs", { signal: controller.signal })]);
        if (!controller.signal.aborted) { setSamples(data.items); setRuns(history); }
      } catch { if (!controller.signal.aborted) setError("작업 목록을 불러오지 못했습니다."); }
      finally { if (!controller.signal.aborted) timer = setTimeout(poll, 5000); }
    }
    void poll();
    api<Device[]>("/devices", { signal: controller.signal }).then(setDevices).catch(() => undefined);
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  async function submit(path: string, body: unknown) {
    setBusy(true); setError(""); setNotice("");
    try {
      await csrfJson(path, body);
      setRuns(await api<Run[]>("/training/runs")); setSelected([]);
      setNotice(path.endsWith("/release") ? "배포 후보로 등록했습니다. 모델 배포 단계에서 선택할 수 있습니다." : "작업을 등록했습니다. 아래 실행 이력에서 진행 상태를 확인하세요.");
    } catch (cause) {
      const code = cause instanceof Error ? cause.message : "";
      setError(code === "TRAINING_BUSY" ? "다른 학습 작업이 진행 중입니다. 완료 후 다시 시도하세요." : code === "LABELED_SAMPLE_REQUIRED" ? "자동 라벨링을 완료한 이미지를 선택하세요." : "요청을 처리하지 못했습니다. 선택 항목과 실행 상태를 확인하세요.");
    } finally { setBusy(false); }
  }
  const active = runs.some(run => ["PREPARING", "TRAINING", "EVALUATING"].includes(run.state));
  return <div className="training-runs">
    <p className="muted">기존 YOLO 박스와 Gemma 태그가 일치하는 이미지를 선별합니다. 자동 생성 라벨이며 사람 검증을 마친 정답은 아닙니다.</p>
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="training-error">{error}</p>}
    {step === "dataset" && <form onSubmit={event => { event.preventDefault(); void submit("/training/runs", { name, sampleIds: selected, confidence: 0.6, auto: auto ? { epochs: autoEpochs, deviceId: deviceId || null } : null }); }}>
      <label>데이터셋 이름<input required maxLength={100} value={name} onChange={event => setName(event.target.value)} placeholder="예: 테스트 영상 1차" /></label>
      <fieldset><legend>자동 라벨링 완료 이미지 · 최근 40장</legend>
        {samples.length === 0 ? <p>사용할 이미지가 없습니다. 수집·자동 라벨링 후 선택할 수 있습니다.</p> : samples.map(sample => <label className="training-sample-choice" key={sample.id}><input type="checkbox" checked={selected.includes(sample.id)} onChange={event => setSelected(current => event.target.checked ? [...current, sample.id] : current.filter(id => id !== sample.id))} />{sample.title} <small>{sample.deviceId}</small></label>)}
      </fieldset>
      <fieldset><legend>자동 진행</legend>
        <label><input type="checkbox" checked={auto} onChange={event => setAuto(event.target.checked)} />구성 후 학습·평가·후보 등록·배포 요청까지 자동으로 진행</label>
        {auto && <><label>학습 횟수(epoch)<input type="number" min={1} max={100} value={autoEpochs} onChange={event => setAutoEpochs(Number(event.target.value))} /></label>
          <label>배포 요청 장치<select value={deviceId} onChange={event => setDeviceId(event.target.value)}><option value="">후보 등록까지만</option>{devices.map(device => <option key={device.deviceId} value={device.deviceId}>{device.name} ({device.deviceId})</option>)}</select></label>
          <p className="muted">평가에서 기준 모델 이상일 때만 후보 등록·배포 요청으로 넘어갑니다. 현장 적용은 IoT에서 명시적으로 실행합니다.</p></>}
      </fieldset>
      <button disabled={busy || active || !name.trim() || selected.length === 0 || (auto && (!Number.isInteger(autoEpochs) || autoEpochs < 1 || autoEpochs > 100))}>선택한 {selected.length}장으로 데이터셋 구성</button>
    </form>}
    {step === "train" && <label>학습 횟수(epoch)<input type="number" min={1} max={100} value={epochs} onChange={event => setEpochs(Number(event.target.value))} /></label>}
    <h3>실행 이력</h3>
    {runs.length === 0 && <p className="muted">아직 구성한 데이터셋이 없습니다.</p>}
    {runs.map(run => {
      const summary: Summary = JSON.parse(run.summary);
      return <article className="training-run-card" key={run.id}><header><h3>{run.name}</h3><span className="control-status">{states[run.state] ?? run.state}</span></header>
        <p>{new Date(run.createdAt).toLocaleString("ko-KR")}{run.automation && " · 자동 진행"}</p>
        {summary.selected !== undefined && <p>선별 {summary.selected}장 · 제외 {summary.excluded}장</p>}
        {summary.error && <p role="alert">{summary.error}</p>}
        {summary.selection && <details><summary>이미지별 선별 사유</summary><ul>{summary.selection.map(item => <li key={item.sampleId}>{item.include ? "포함" : "제외"} · {item.reason} · {item.sampleId.slice(0, 8)}</li>)}</ul></details>}
        {summary.modelSha256 && <p>후보 모델 SHA-256: <code>{summary.modelSha256.slice(0, 16)}…</code></p>}
        {summary.evaluation && <p>mAP50–95 변화: {summary.evaluation.deltaMap50_95.toFixed(4)} · {summary.evaluation.eligibleForPromotion ? "평가 기준 통과" : "평가 기준 미달"}</p>}
        {summary.automation && <p>자동 진행: {[summary.automation.release, summary.automation.deployment].filter(Boolean).map(value => automationText[value!] ?? value).join(" → ")}{summary.automation.reason && ` (${summary.automation.reason})`}</p>}
        {step === "train" && run.state === "DATASET_READY" && <button disabled={busy || active || !Number.isInteger(epochs) || epochs < 1 || epochs > 100} onClick={() => void submit(`/training/runs/${run.id}/train`, { epochs })}>후보 모델 학습 시작</button>}
        {step === "train" && run.state === "EVALUATED" && summary.evaluation?.eligibleForPromotion && <button disabled={busy} onClick={() => void submit(`/training/runs/${run.id}/release`, {})}>배포 후보 등록</button>}
        {step === "train" && run.state === "TRAINED" && <button disabled={busy || active} onClick={() => void submit(`/training/runs/${run.id}/evaluate`, {})}>기준 모델과 평가</button>}
      </article>;
    })}
  </div>;
}
