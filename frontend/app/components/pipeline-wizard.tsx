"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, csrfJson, type Device } from "../../lib/api";
import PipelineDemo from "./pipeline-demo";
import SideNav from "./side-nav";
import UploadPanel, { type UploadResult } from "./upload-panel";

type SourceKind = "IOT" | "UPLOAD" | "EXISTING" | "ROBOFLOW";
type BaseModel = { key: string; label: string; description: string; kind: "SITE" | "PRETRAINED" | "RELEASE"; size: string; map: number | null; createdAt: string | null };
const modelKind = { SITE: "현장 기본", PRETRAINED: "사전학습", RELEASE: "등록 후보" };
type External = { id: string; url: string; workspace: string; project: string; version: number | null; status: string; name: string | null; classes: string | null; mapping: string | null; counts: string | null; error: string | null; phase: string | null; progress: number | null };
const CLASSES = ["person", "safety_vest", "traffic_cone", "hard_hat"];
const STEPS = ["데이터 수집", "자동 라벨링", "데이터셋 구성", "학습 옵션", "확인"];
const phaseName: Record<string, string> = { EXPORT: "Roboflow에서 내보내기 준비 중", DOWNLOAD: "내려받는 중", EXTRACT: "압축 푸는 중", VALIDATE: "데이터셋 검사 중" };
const externalStatus: Record<string, string> = { QUEUED: "대기", RUNNING: "내려받는 중", READY: "준비됨", FAILED: "실패" };
const errors: Record<string, string> = {
  INVALID_ROBOFLOW_URL: "universe.roboflow.com/워크스페이스/프로젝트 형식의 주소를 입력하세요.",
  IOT_SOURCE_REQUIRED: "IoT 장치와 수집 장수를 선택하세요.", UPLOAD_SOURCE_REQUIRED: "먼저 이미지나 영상을 올리세요.",
  DEVICE_NOT_REGISTERED: "등록되지 않은 장치입니다.", EXTERNAL_NOT_AVAILABLE: "사용할 수 없는 로보플로우 데이터셋이 포함되어 있습니다.",
  ALREADY_IN_BASELINE: "construction-site-safety v27은 기준 데이터셋에 이미 전부 들어 있어 따로 불러올 필요가 없습니다.",
  BASE_MODEL_NOT_FOUND: "선택한 베이스 모델을 찾을 수 없습니다.", ROBOFLOW_SOURCE_REQUIRED: "로보플로우 데이터셋을 하나 이상 선택하세요.",
};
const parse = <T,>(value: string | null, fallback: T): T => { try { return value ? JSON.parse(value) : fallback; } catch { return fallback; } };

export default function PipelineWizard() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [introDone, setIntroDone] = useState(false);
  const [kind, setKind] = useState<SourceKind>("UPLOAD");
  const [devices, setDevices] = useState<Device[]>([]);
  const [chosenDevices, setChosenDevices] = useState<string[]>([]);
  const [target, setTarget] = useState(20);
  const [includeExisting, setIncludeExisting] = useState(false);
  const [uploaded, setUploaded] = useState<UploadResult>({ sampleIds: [], importIds: [] });
  const [useRoboflow, setUseRoboflow] = useState(false);
  const [externals, setExternals] = useState<External[]>([]);
  const [chosenExternal, setChosenExternal] = useState<string[]>([]);
  const [roboflowUrl, setRoboflowUrl] = useState("");
  const [baseModels, setBaseModels] = useState<BaseModel[]>([]);
  const [baseModel, setBaseModel] = useState("default");
  const [epochs, setEpochs] = useState(100);
  const [confidence, setConfidence] = useState(0.6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fail = useCallback((cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) router.replace("/login");
    else setError(cause instanceof ApiError ? errors[cause.message] ?? `요청을 처리하지 못했습니다 (${cause.message}).` : "서버에 연결하지 못했습니다.");
  }, [router]);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [deviceList, externalList, models] = await Promise.all([api<Device[]>("/devices", { signal }), api<External[]>("/training/external-datasets", { signal }), api<BaseModel[]>("/training/base-models", { signal })]);
    if (!signal?.aborted) { setDevices(deviceList); setExternals(externalList); setBaseModels(models); }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(controller.signal); } catch (cause) { if (!controller.signal.aborted) fail(cause); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh, fail]);

  const sourceReady = kind === "IOT" ? chosenDevices.length > 0 && target >= 1 : kind === "UPLOAD" ? uploaded.sampleIds.length + uploaded.importIds.length > 0 : true;
  const external = kind === "ROBOFLOW" || useRoboflow ? chosenExternal : [];
  const stepReady = [sourceReady && (kind !== "ROBOFLOW" && !useRoboflow || chosenExternal.length > 0), true, true, epochs >= 1 && epochs <= 100, true][step];
  async function addRoboflow() {
    setError("");
    try {
      const created = await csrfJson<External>("/training/external-datasets", { url: roboflowUrl });
      setRoboflowUrl(""); setChosenExternal(current => current.includes(created.id) ? current : [...current, created.id]); await refresh();
    } catch (cause) { fail(cause); }
  }
  async function removeExternal(id: string) {
    setError("");
    try {
      const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
      await api(`/training/external-datasets/${id}`, { method: "DELETE", headers: { [csrf.headerName]: csrf.token } });
      setChosenExternal(current => current.filter(item => item !== id)); await refresh();
    } catch (cause) { fail(cause); }
  }
  async function start() {
    setBusy(true); setError("");
    try {
      await csrfJson("/training/pipelines", {
        sourceKind: kind,
        source: kind === "IOT" ? { deviceIds: chosenDevices, targetCount: target, includeExisting } : kind === "UPLOAD" ? uploaded : {},
        external: external.map(id => ({ id })),
        options: { baseModel, epochs, confidence },
      });
      router.push("/ai-training");
    } catch (cause) { fail(cause); } finally { setBusy(false); }
  }
  const model = baseModels.find(m => m.key === baseModel);
  const sourceSummary = kind === "IOT" ? `IoT ${chosenDevices.join(", ")} · ${includeExisting ? "기존 이미지 포함 " : "지금부터 "}${target}장` : kind === "UPLOAD" ? `직접 올리기 · 이미지 ${uploaded.sampleIds.length}장, 영상 ${uploaded.importIds.length}개` : kind === "ROBOFLOW" ? `로보플로우 데이터셋 ${external.length}개` : "이전 학습에 쓰지 않은 라벨링 완료 이미지(최대 200장)";

  return <div className="app-shell control-shell">
    <SideNav active="ai-training" />
    <main className="training-main control-room">
      <Link className="training-back" href="/ai-training">← 파이프라인 목록</Link>
      <header className="collection-heading"><div><p className="eyebrow">새로운 파이프라인</p><h1>파이프라인 만들기</h1></div></header>
      {!introDone ? <section className="wizard-intro">
        <PipelineDemo />
        <div className="training-actions wizard-nav"><span /><button className="primary" onClick={() => setIntroDone(true)}>다음</button></div>
      </section> : <>
      <ol className="wizard-steps">{STEPS.map((title, index) => <li key={title} className={index === step ? "current" : index < step ? "done" : ""}><span>{index < step ? "✓" : index + 1}</span>{title}</li>)}</ol>
      {error && <p className="training-error" role="alert">{error}</p>}
      <section className="training-card wizard-panel">
        {step === 0 && <>
          <h2>데이터를 어떻게 모을까요?</h2>
          <p className="muted">IoT 장치가 보내는 이미지는 목록 위의 자동 파이프라인이 매일 학습합니다. 여기서는 업로드·로보플로우로 따로 실험합니다.</p>
          <div className="wizard-choices">{([["UPLOAD", "직접 올리기", "이미지·영상 파일·유튜브 링크를 올립니다."], ["EXISTING", "기존 수집 이미지 사용", "이미 라벨링이 끝난, 아직 학습에 쓰지 않은 이미지를 씁니다."], ["ROBOFLOW", "로보플로우 데이터셋", "Roboflow Universe 링크로 사람이 라벨링한 데이터셋을 불러옵니다."]] as [SourceKind, string, string][]).map(([value, title, text]) =>
            <button key={value} className={`wizard-choice ${kind === value ? "chosen" : ""}`} aria-pressed={kind === value} onClick={() => setKind(value)}><strong>{title}</strong><span>{text}</span></button>)}</div>
          {kind === "IOT" && <div className="wizard-detail">
            {devices.length === 0 ? <p className="muted">등록된 IoT 장치가 없습니다.</p> : devices.map(device => <label key={device.deviceId} className="check-label"><input type="checkbox" checked={chosenDevices.includes(device.deviceId)} onChange={e => setChosenDevices(current => e.target.checked ? [...current, device.deviceId] : current.filter(id => id !== device.deviceId))} />{device.name} ({device.deviceId}) <span className={device.online ? "green" : "gray"}>● {device.online ? "온라인" : "오프라인"}</span></label>)}
            <div className="training-actions"><label>수집할 장수 <input type="number" min={1} max={200} value={target} onChange={e => setTarget(Number(e.target.value))} /></label>
              <label className="check-label"><input type="checkbox" checked={includeExisting} onChange={e => setIncludeExisting(e.target.checked)} />이미 수집된 이미지도 포함</label></div>
            <p className="muted">IoT 감시·수집이 켜져 있어야 이미지가 들어옵니다. 목표 장수가 모일 때까지 파이프라인은 수집 단계에서 기다립니다.</p>
          </div>}
          {kind === "UPLOAD" && <div className="wizard-detail">
            <p className="muted">올린 항목: 이미지 {uploaded.sampleIds.length}장 · 영상 {uploaded.importIds.length}개</p>
            <UploadPanel onUploaded={() => undefined} onResult={result => setUploaded(current => ({ sampleIds: [...new Set([...current.sampleIds, ...result.sampleIds])], importIds: [...current.importIds, ...result.importIds] }))} />
          </div>}
          <div className="wizard-detail">
            {kind === "ROBOFLOW" ? <h3>로보플로우 데이터셋</h3> : <label className="check-label"><input type="checkbox" checked={useRoboflow} onChange={e => setUseRoboflow(e.target.checked)} />로보플로우 데이터셋도 함께 사용 (선택)</label>}
            {(kind === "ROBOFLOW" || useRoboflow) && <>
            <div className="training-actions"><label className="wizard-url">Roboflow Universe 주소 <input type="url" value={roboflowUrl} maxLength={300} placeholder="https://universe.roboflow.com/워크스페이스/프로젝트/dataset/버전" onChange={e => setRoboflowUrl(e.target.value)} /></label><button disabled={!roboflowUrl.trim()} onClick={() => void addRoboflow()}>불러오기</button></div>
            {externals.length === 0 ? <p className="muted">불러온 데이터셋이 없습니다.</p> : <div className="external-list">{externals.map(item => {
              const classes = parse<string[]>(item.classes, []); const mapping = parse<(number | null)[]>(item.mapping, []); const counts = parse<Record<string, number>>(item.counts, {});
              const chosen = chosenExternal.includes(item.id);
              return <article key={item.id} className={`external-item ${chosen ? "chosen" : ""}`}>
                <label className="check-label"><input type="checkbox" disabled={item.status === "FAILED"} checked={chosen} onChange={e => setChosenExternal(current => e.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} />
                  <strong>{item.workspace}/{item.project}{item.version ? ` v${item.version}` : ""}</strong> <span className="collection-badge">{externalStatus[item.status] ?? item.status}</span></label>
                {item.status === "READY" && <p className="muted">train {counts.train ?? 0} · val {counts.val ?? 0}장 · 클래스 매핑: {classes.map((c, i) => `${c} → ${mapping[i] == null ? "제외" : CLASSES[mapping[i] as number]}`).join(", ")}</p>}
                {item.status === "RUNNING" && <div className="external-progress">
                  <span>{phaseName[item.phase ?? "EXPORT"]}{item.progress != null ? ` ${item.progress}%` : ""}</span>
                  <div className={`epoch-bar live ${item.progress == null ? "indeterminate" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.progress ?? undefined}><span style={{ width: `${item.progress ?? 100}%` }} /></div>
                </div>}
                {item.status === "QUEUED" && <p className="muted">다른 작업이 끝나면 내려받기를 시작합니다.</p>}
                {item.error && <p className="training-error">{item.error}</p>}
                {item.status === "FAILED" && <button className="danger" onClick={() => void removeExternal(item.id)}>삭제</button>}
              </article>;
            })}</div>}
            <p className="muted">기준 데이터셋(construction-site-safety v27 + 현장 영상 프레임)은 항상 기본으로 포함됩니다. 선택한 데이터셋은 train·val 이미지를 모두 학습에 씁니다. 클래스는 이름으로 자동 연결합니다(예: Hardhat → hard_hat, NO-Hardhat → 제외). 내려받기가 끝나지 않아도 다음으로 넘어갈 수 있으며, 파이프라인이 데이터셋 단계에서 기다립니다.</p>
            </>}
          </div>
        </>}
        {step === 1 && <div className="wizard-auto"><span className="wizard-auto-icon">✓</span><div><h2>자동 라벨링은 알아서 진행됩니다</h2><p className="muted">{kind === "ROBOFLOW" ? "로보플로우 데이터셋은 이미 사람이 라벨링한 박스가 있어 이 단계를 건너뜁니다." : "수집된 이미지마다 Gemma가 장면 설명과 태그(person·safety_vest·traffic_cone·hard_hat)를 만듭니다. 로보플로우 데이터는 사람 라벨을 그대로 씁니다."} 고를 옵션이 없습니다.</p></div></div>}
        {step === 2 && <>
          <div className="wizard-auto"><span className="wizard-auto-icon">✓</span><div><h2>데이터셋 구성은 자동입니다</h2><p className="muted">{kind === "ROBOFLOW" ? "선택한 로보플로우 데이터셋을 4개 클래스로 바꿔 기준 데이터셋과 합칩니다." : "기존 YOLO 모델이 박스 초안을 만들고 Gemma 태그와 일치하는 이미지만 골라 기준 데이터셋과 합칩니다."} 평가용 test 세트는 기준 자료를 그대로 씁니다.</p></div></div>
          <p className="muted">함께 쓰는 로보플로우 데이터셋: {external.length ? externals.filter(item => external.includes(item.id)).map(item => `${item.workspace}/${item.project}${item.version ? ` v${item.version}` : ""}`).join(", ") : "없음"} · 바꾸려면 데이터 수집 단계로 돌아가세요.</p>
        </>}
        {step === 3 && <>
          <h2>학습 옵션</h2>
          <p className="wizard-name-note">✨ 모델 이름은 따로 적지 않습니다. 학습이 끝나면 Gemma4가 데이터 구성과 결과를 보고 추천한 이름으로 자동 설정됩니다.</p>
          <h3>베이스 모델</h3>
          <p className="muted">어느 모델에서 학습을 시작할지 고르세요. 평가를 통과해 등록된 후보가 생기면 이 목록에 함께 나옵니다.</p>
          <div className="table-scroll"><table className="model-pick">
            <thead><tr><th /><th>모델</th><th>종류</th><th>규모</th><th>기준 test mAP50–95</th><th>설명</th></tr></thead>
            <tbody>{baseModels.map(item => <tr key={item.key} className={baseModel === item.key ? "chosen" : ""} onClick={() => setBaseModel(item.key)}>
              <td><input type="radio" name="base-model" aria-label={item.label} checked={baseModel === item.key} onChange={() => setBaseModel(item.key)} /></td>
              <td><strong>{item.label}</strong>{item.key === "default" && <span className="collection-badge">추천</span>}{item.createdAt && <small className="muted"> {new Date(item.createdAt).toLocaleDateString("ko-KR")} 등록</small>}</td>
              <td>{modelKind[item.kind] ?? item.kind}</td><td>{item.size}</td><td>{item.map == null ? "—" : item.map.toFixed(3)}</td><td className="muted">{item.description}</td>
            </tr>)}</tbody>
          </table></div>
          <div className="training-actions">
            <label>Epoch <input type="number" min={1} max={100} value={epochs} onChange={e => setEpochs(Number(e.target.value))} /></label>
            <label>선별 신뢰도 {confidence.toFixed(2)} <input type="range" min={0.5} max={0.95} step={0.05} value={confidence} onChange={e => setConfidence(Number(e.target.value))} /></label>
          </div>
          <p className="muted">선별: 현장 기본 YOLO 모델이 수집 이미지마다 박스 초안을 그리고, 모든 박스가 선별 신뢰도 이상이며 박스 클래스가 Gemma 태그와 일치하는 이미지만 학습 데이터에 넣습니다. 신뢰도를 높이면 더 확실한 이미지만 남아 장수가 줄어듭니다. 로보플로우 이미지는 선별하지 않습니다.</p>
        </>}
        {step === 4 && <>
          <h2>확인</h2>
          <dl className="wizard-summary">
            <dt>이름</dt><dd>학습 후 Gemma4 자동 추천</dd>
            <dt>데이터 수집</dt><dd>{sourceSummary}</dd>
            <dt>자동 라벨링</dt><dd>{kind === "ROBOFLOW" ? "건너뜀(사람 라벨 사용)" : "Gemma 자동"}</dd>
            <dt>데이터셋 구성</dt><dd>{kind === "ROBOFLOW" ? "기준 데이터셋" : "YOLO·Gemma 자동 선별 + 기준 데이터셋"}{external.length ? ` + 로보플로우 ${external.length}개(train·val 전체)` : ""}</dd>
            <dt>학습</dt><dd>{model?.label ?? baseModel} · {epochs} epoch · 신뢰도 {confidence.toFixed(2)}</dd>
            <dt>배포</dt><dd>평가를 통과하면 목록의 배포 버튼으로 IoT 장치에 보냅니다.</dd>
          </dl>
        </>}
        <div className="training-actions wizard-nav">
          <button disabled={busy} onClick={() => step === 0 ? setIntroDone(false) : setStep(step - 1)}>이전</button>
          {step < STEPS.length - 1 ? <button className="primary" disabled={!stepReady} onClick={() => setStep(step + 1)}>다음</button>
            : <button className="primary" disabled={busy} onClick={() => void start()}>{busy ? "시작하는 중…" : "파이프라인 시작"}</button>}
        </div>
      </section>
      </>}
    </main>
  </div>;
}
