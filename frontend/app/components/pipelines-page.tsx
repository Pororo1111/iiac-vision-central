"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useState } from "react";
import { api, ApiError, csrfJson, type Device } from "../../lib/api";
import { STAGES } from "./pipeline-demo";
import SideNav from "./side-nav";
import TrainingCharts, { duration, type Metrics } from "./training-charts";
import AutoPipeline from "./auto-pipeline";

type Pipeline = { id: string; name: string; sourceKind: "IOT" | "UPLOAD" | "EXISTING" | "ROBOFLOW" | "AUTO"; source: string; external: string; options: string; stage: string; runId: string | null; sampleCount: number; labeledCount: number; message: string | null; createdAt: string; nameStatus: string };
type Run = { id: string; state: string; summary: string; baseModel: string };
type Release = { id: string; sha256: string };
type Deployment = { id: string; deviceId: string; status: string; message: string; updatedAt: string };
type View = { pipeline: Pipeline; run: Run | null; release: Release | null; deployments: Deployment[] };
/** 학습 중인 실행의 완료 epoch와 남은 시간(초). 첫 epoch가 끝나기 전에는 remaining이 null이다. */
type Eta = { done: number; remaining: number | null };
/** 준비 작업(PREPARING)의 현재 단계. external-label이면 외부 라벨 보정, dataset이면 조합 중이다. */
type Step = { step: string; current: number; total: number };
type Cell = { status: "pending" | "running" | "success" | "failed" | "unstable" | "skipped"; text: string };

const sourceName = { IOT: "IoT 장치 수집", UPLOAD: "직접 올리기", EXISTING: "기존 수집 이미지", ROBOFLOW: "로보플로우 데이터셋", AUTO: "자동 · IoT 이미지" };
const deployName: Record<string, string> = { PENDING: "배포 대기", DOWNLOADING: "내려받는 중", VALIDATING: "검증 중", APPLYING: "적용 중", APPLIED: "적용 완료", FAILED: "배포 실패", ROLLED_BACK: "되돌림", CANCELED: "배포 취소" };
const cancelableDeploy = ["PENDING", "DOWNLOADING", "VALIDATING"];
const parse = (value: string | null | undefined) => { try { return value ? JSON.parse(value) : {}; } catch { return {}; } };
const date = (value: string) => new Date(value).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

type Reference = { deltaMap50_95?: number; images?: number };
const reference = (name: string, value?: Reference) =>
  typeof value?.deltaMap50_95 === "number" ? ` · ${name} ${value.images}장 Δ ${value.deltaMap50_95 >= 0 ? "+" : ""}${value.deltaMap50_95.toFixed(3)}` : "";
/** 현장·외부 test 참고 점수. 배포 판정에는 쓰지 않는다. */
const site = (evaluation: { site?: Reference; external?: Reference }) => reference("현장", evaluation.site) + reference("외부", evaluation.external);

/** 파이프라인·학습 실행 상태를 다섯 단계 칸으로 바꾼다. */
export function cells(view: View, eta?: Eta, step?: Step): Cell[] {
  const { pipeline: p, run, release, deployments } = view;
  const summary = parse(run?.summary);
  const options = parse(p.options);
  const externalCount = parse(p.external).length ?? 0;
  const collect: Cell = { status: "success", text: p.sourceKind === "AUTO" ? `IoT 새 이미지 ${p.sampleCount}장` : p.sourceKind === "EXISTING" ? "기존 이미지" : p.sourceKind === "ROBOFLOW" ? `로보플로우 ${externalCount}개` : `${p.sampleCount}장${externalCount ? ` + 로보플로우 ${externalCount}개` : ""}` };
  // 로보플로우 데이터셋은 빠진 클래스 채움·안전모 박스 맞춤을 라벨링 단계에서 한다.
  const corrected = Object.values((summary.externalLabel ?? {}) as Record<string, { included: number; excluded: number }>);
  const correction = corrected.length ? `외부 보정 ${corrected.reduce((a, b) => a + b.included, 0).toLocaleString()}장 · 제외 ${corrected.reduce((a, b) => a + b.excluded, 0).toLocaleString()}장` : "";
  const siteLabel = p.sourceKind === "AUTO" ? "수신 즉시 Gemma 완료" : p.sourceKind === "EXISTING" || p.sourceKind === "ROBOFLOW" ? "" : `${p.labeledCount}/${p.sampleCount}장`;
  const labelText = [siteLabel, correction].filter(Boolean).join(" · ");
  const label: Cell = labelText ? { status: "success", text: labelText } : externalCount ? { status: "pending", text: "보정 대기" } : { status: "skipped", text: "건너뜀" };
  const pending = (text = "대기"): Cell => ({ status: "pending", text });
  if (p.stage === "CANCELLED") return STAGES.map(() => ({ status: "skipped", text: "취소" }));
  if (p.stage === "COLLECTING") return [{ status: "running", text: p.message ?? "수집 중" }, pending(), pending(), pending(), pending()];
  if (p.stage === "LABELING") return [collect, { status: "running", text: p.message ?? "라벨링 중" }, pending(), pending(), pending()];
  if (p.stage === "DATASET") return [collect, label, { status: "running", text: p.message ?? "구성 중" }, pending(), pending()];
  if (p.stage === "FAILED" || !run) return [collect, label, { status: "failed", text: p.message ?? "실패" }, pending(), pending()];
  const external = Object.values((summary.external ?? {}) as Record<string, number>).reduce((a, b) => a + b, 0);
  const inherited = Object.values((summary.inherited ?? {}) as Record<string, number>).reduce((a, b) => a + b, 0);
  const split = summary.site ?? {};
  const dataset: Cell = { status: "success", text: `선별 ${summary.selected ?? 0}장${summary.selected ? ` (train ${split.train ?? 0}·val ${split.val ?? 0}·test ${split.test ?? 0})` : ""}${external ? ` + 외부 ${external}장` : ""}${inherited ? ` + 이전 모델 자료 ${inherited}장` : ""}` };
  const train = `${options.epochs ?? "?"} epoch`;
  if (run.state === "FAILED") {
    const failed: Cell = summary.stopped ? { status: "skipped", text: "관리자 중단" } : { status: "failed", text: String(summary.error ?? "실패").slice(0, 60) };
    if (summary.failedAction === "PREPARE") return [collect, label, failed, pending(), pending()];
    return [collect, label, summary.selected === undefined ? { status: "success", text: "구성 완료" } : dataset, failed, pending()];
  }
  if (run.state === "PREPARING" && step?.step === "external-label")
    return [collect, { status: "running", text: `외부 라벨 보정 ${step.current.toLocaleString()}/${step.total ? step.total.toLocaleString() : "?"}장` }, pending(), pending(), pending()];
  if (run.state === "PREPARING") return [collect, label, { status: "running", text: "YOLO 선별·조합 중" }, pending(), pending()];
  if (run.state === "DATASET_READY") return [collect, label, dataset, { status: "running", text: `학습 준비 중 · ${train}` }, pending()];
  if (run.state === "TRAINING") {
    const total = options.epochs ?? "?";
    const time = !eta || eta.remaining === null ? "예상 시간 계산 중" : `약 ${duration(eta.remaining)} 남음`;
    return [collect, label, dataset, { status: "running", text: `학습 중 · ${eta?.done ?? 0}/${total} epoch · ${time}` }, pending()];
  }
  if (run.state === "TRAINED" || run.state === "EVALUATING") return [collect, label, dataset, { status: "running", text: "평가 중" }, pending()];
  const evaluation = summary.evaluation ?? {};
  const delta = typeof evaluation.deltaMap50_95 === "number" ? `Δ mAP ${evaluation.deltaMap50_95 >= 0 ? "+" : ""}${evaluation.deltaMap50_95.toFixed(4)}` : "";
  if (!release) return [collect, label, dataset, { status: "unstable", text: `평가 미달 ${delta}${site(evaluation)}` }, { status: "skipped", text: "배포 불가" }];
  const latest = deployments[0];
  const deploy: Cell = !latest ? { status: "pending", text: "배포 가능" }
    : { status: latest.status === "APPLIED" ? "success" : ["FAILED", "ROLLED_BACK", "CANCELED"].includes(latest.status) ? "failed" : "running", text: `${latest.deviceId} · ${deployName[latest.status] ?? latest.status}` };
  return [collect, label, dataset, { status: "success", text: `통과 ${delta}${site(evaluation)}` }, deploy];
}

export default function PipelinesPage() {
  const router = useRouter();
  const [views, setViews] = useState<View[] | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deploying, setDeploying] = useState<View | null>(null);
  const [device, setDevice] = useState("");
  const [policies, setPolicies] = useState<Record<string, { mode: string; pinnedModelId: string | null }>>({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const fail = useCallback((cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) router.replace("/login");
    else if (cause instanceof ApiError && cause.message === "DEPLOYMENT_ACTIVE") setError("이 장치에 진행 중인 배포가 있습니다. 끝난 뒤 다시 요청하세요.");
    else if (cause instanceof ApiError && cause.status === 409) setError(`요청을 처리할 수 없습니다 (${cause.message}).`);
    else setError("서버에서 정보를 받지 못했습니다. 연결 상태를 확인하세요.");
  }, [router]);
  const [etas, setEtas] = useState<Record<string, Eta>>({});
  const [steps, setSteps] = useState<Record<string, Step>>({});
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [list, deviceList] = await Promise.all([api<View[]>("/training/pipelines", { signal }), api<Device[]>("/devices", { signal })]);
    // 학습 중인 실행은 results.csv의 epoch별 누적 시간으로 남은 시간을 추정한다.
    const training = list.filter(view => view.run?.state === "TRAINING");
    const estimates = await Promise.all(training.map(async view => {
      const total = Number(parse(view.pipeline.options).epochs) || 1;
      try {
        const metrics = await api<Metrics>(`/training/runs/${view.run!.id}/metrics`, { signal });
        const epoch = metrics.columns.indexOf("epoch"), time = metrics.columns.indexOf("time");
        const last = metrics.rows[metrics.rows.length - 1];
        const done = last && epoch >= 0 ? last[epoch] : 0;
        const elapsed = last && time >= 0 ? last[time] : 0;
        return [view.run!.id, { done, remaining: done > 0 && elapsed > 0 ? elapsed / done * Math.max(0, total - done) : null }] as const;
      } catch { return [view.run!.id, { done: 0, remaining: null }] as const; }
    }));
    const preparing = await Promise.all(list.filter(view => view.run?.state === "PREPARING").map(async view => {
      try { return [view.run!.id, await api<Step>(`/training/runs/${view.run!.id}/progress`, { signal })] as const; }
      catch { return [view.run!.id, { step: "", current: 0, total: 0 }] as const; }
    }));
    if (!signal?.aborted) { setViews(list); setDevices(deviceList); setEtas(Object.fromEntries(estimates)); setSteps(Object.fromEntries(preparing)); }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(controller.signal); if (!controller.signal.aborted) setError(""); } catch (cause) { if (!controller.signal.aborted) fail(cause); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh, fail]);
  async function openDeploy(view: View) {
    setDeploying(view); setDevice(devices[0]?.deviceId ?? "");
    const rows = await api<{ policy: { deviceId: string; mode: string; pinnedModelId: string | null } }[]>("/training/device-policies").catch(() => []);
    setPolicies(Object.fromEntries(rows.map(row => [row.policy.deviceId, row.policy])));
  }
  // 고정 장치는 고정 모델을 이 모델로 바꾸는 방식으로만 배포한다. 자동 장치는 일반 배포 요청을 보낸다.
  async function deploy() {
    if (!deploying?.release || !device) return;
    const pinned = (policies[device]?.mode ?? "PINNED") === "PINNED";
    if (pinned && !window.confirm(`${device}는 고정 장치입니다. 고정 모델을 ${deploying.pipeline.name}(으)로 바꾸고 배포할까요? 적용 전인 다른 배포는 취소됩니다.`)) return;
    setError(""); setMessage("");
    try {
      if (pinned) {
        const result = await csrfJson<{ deployment: string }>(`/training/device-policies/${device}`, { mode: "PINNED", modelId: deploying.release.id, note: `파이프라인 ${deploying.pipeline.name}에서 고정` });
        setMessage(result.deployment === "ALREADY_ACTIVE" ? `${device}가 이미 이 모델을 실행 중이라 고정만 바꿨습니다.` : `${device}의 고정 모델을 ${deploying.pipeline.name}(으)로 바꾸고 배포를 요청했습니다.`);
      } else {
        await csrfJson("/training/deployments", { modelId: deploying.release.id, deviceId: device });
        setMessage(`${deploying.pipeline.name} 모델을 ${device}에 배포 요청했습니다. IoT가 heartbeat로 받아 적용하면 상태가 바뀝니다.`);
      }
      setDeploying(null); await refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.message === "DEPLOYMENT_APPLYING") setError("장치가 다른 모델을 적용하는 중입니다. 끝난 뒤 다시 요청하세요.");
      else fail(cause);
    }
  }
  async function stop(view: View) {
    if (!view.run || !window.confirm(`${view.pipeline.name} 학습을 중단할까요? 진행 중인 학습 결과는 버려집니다.`)) return;
    setError(""); setMessage("");
    try { await csrfJson(`/training/runs/${view.run.id}/stop`, {}); setMessage(`${view.pipeline.name} 학습을 중단했습니다.`); await refresh(); } catch (cause) { fail(cause); }
  }
  async function remove(view: View) {
    if (!window.confirm(`${view.pipeline.name} 파이프라인을 삭제할까요? 학습 기록과 학습 결과 파일이 지워지며 되돌릴 수 없습니다. 수집·업로드한 이미지는 남습니다.`)) return;
    setError(""); setMessage("");
    try {
      const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
      await api(`/training/pipelines/${view.pipeline.id}`, { method: "DELETE", headers: { [csrf.headerName]: csrf.token } });
      setMessage(`${view.pipeline.name} 파이프라인을 삭제했습니다.`); await refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.message === "PIPELINE_ACTIVE") setError("진행 중인 파이프라인은 먼저 취소하거나 중단하세요.");
      else if (cause instanceof ApiError && cause.message === "MODEL_REGISTERED") setError("평가를 통과해 등록된 모델이 있는 파이프라인은 삭제할 수 없습니다.");
      else fail(cause);
    }
  }
  async function cancelDeploy(view: View) {
    const target = view.deployments.find(d => cancelableDeploy.includes(d.status));
    if (!target || !window.confirm(`${target.deviceId}에 대한 배포 요청을 취소할까요? 적용 단계에 들어가기 전까지만 취소할 수 있습니다.`)) return;
    setError(""); setMessage("");
    try { await csrfJson(`/training/deployments/${target.id}/cancel`, {}); setMessage(`${target.deviceId} 배포 요청을 취소했습니다.`); await refresh(); }
    catch (cause) {
      if (cause instanceof ApiError && cause.message === "DEPLOYMENT_NOT_CANCELABLE") { setError("이미 적용 단계에 들어갔거나 끝난 배포는 취소할 수 없습니다."); await refresh(); }
      else fail(cause);
    }
  }
  async function cancel(view: View) {
    try { await csrfJson(`/training/pipelines/${view.pipeline.id}/cancel`, {}); await refresh(); } catch (cause) { fail(cause); }
  }
  function rowOf(view: View) {
    const options = parse(view.pipeline.options);
    return {
      cells: cells(view, view.run ? etas[view.run.id] : undefined, view.run ? steps[view.run.id] : undefined), epochs: Number(options.epochs) || 1,
      meta: `${sourceName[view.pipeline.sourceKind]} · ${options.baseModel === "default" ? "기본 모델" : String(options.baseModel ?? "").replace("release:", "후보 ").slice(0, 20)} · ${options.epochs ?? "?"} epoch · ${date(view.pipeline.createdAt)}`,
      canDeploy: !!view.release && !view.deployments.some(d => ["PENDING", "DOWNLOADING", "VALIDATING", "APPLYING"].includes(d.status)),
      canCancelDeploy: view.deployments.some(d => cancelableDeploy.includes(d.status)),
      active: !!view.run && ["PREPARING", "DATASET_READY", "TRAINING", "TRAINED", "EVALUATING"].includes(view.run.state),
      hasMetrics: !!view.run && view.run.state !== "PREPARING" && (view.run.state !== "FAILED" || parse(view.run.summary).failedAction !== "PREPARE"),
      deletable: !view.release && !["COLLECTING", "LABELING", "DATASET"].includes(view.pipeline.stage) && !(view.run && ["PREPARING", "DATASET_READY", "TRAINING", "TRAINED", "EVALUATING"].includes(view.run.state)),
      open: expanded[view.pipeline.id] ?? view.run?.state === "TRAINING",
    };
  }
  const nameOf = (view: View) => <>{view.pipeline.sourceKind === "AUTO" && <span className="auto-badge">자동</span>}{view.pipeline.nameStatus === "DONE" ? <strong>{view.pipeline.name}</strong>
    : <strong className="name-pending">{view.pipeline.nameStatus === "GENERATING" ? "✨ Gemma가 이름 짓는 중…" : "✨ Gemma 이름 추천 대기"}</strong>}</>;
  const actions = (view: View, r: ReturnType<typeof rowOf>) => <>
    {view.release && <button className="primary" disabled={!r.canDeploy} onClick={() => void openDeploy(view)}>배포</button>}
    {r.canCancelDeploy && <button onClick={() => void cancelDeploy(view)}>배포 취소</button>}
    {["COLLECTING", "LABELING", "DATASET"].includes(view.pipeline.stage) && <button onClick={() => void cancel(view)}>취소</button>}
    {r.active && <button className="danger" onClick={() => void stop(view)}>중단</button>}
    {r.deletable && <button className="danger" onClick={() => void remove(view)}>삭제</button>}
    {r.hasMetrics && <button aria-expanded={r.open} onClick={() => setExpanded(current => ({ ...current, [view.pipeline.id]: !r.open }))}>{r.open ? "지표 닫기" : "지표"}</button>}
  </>;
  return <div className="app-shell control-shell">
    <SideNav active="ai-training" />
    <main className="training-main control-room">
      <header className="collection-heading pipeline-heading"><div><p className="eyebrow">AI 학습</p><h1>학습 파이프라인</h1><p className="muted">IoT 이미지는 자동 파이프라인이 매일 학습합니다. 업로드·로보플로우 데이터로 따로 학습하려면 새로운 파이프라인을 만드세요.</p></div>
        <Link className="primary pipeline-new" href="/ai-training/new">+ 새로운 파이프라인</Link></header>
      {error && <p className="training-error" role="alert">{error}</p>}{message && <p className="training-success" role="status">{message}</p>}
      <AutoPipeline onStarted={() => void refresh().catch(fail)} />
      <section className="training-card">
        <div className="collection-heading"><div><h2>파이프라인 목록</h2><p className="muted">단계별 상태 · 5초마다 갱신</p></div>
          <div className="stage-legend">{[["success", "완료"], ["running", "진행 중"], ["unstable", "평가 미달"], ["failed", "실패"], ["pending", "대기"]].map(([key, name]) => <span key={key}><i className={`stage-swatch ${key}`} />{name}</span>)}</div></div>
        {views === null ? <p className="muted">불러오는 중입니다.</p> : views.length === 0 ? <div className="collection-empty"><strong>아직 만든 파이프라인이 없습니다.</strong><p className="muted">위의 “새로운 파이프라인”으로 시작하세요.</p></div> :
          <>
          {/* PC: 젠킨스식 스테이지 표. 칸 안에서 줄바꿈한다. */}
          <div className="table-scroll stage-desktop"><table className="stage-view"><thead><tr><th>파이프라인</th>{STAGES.map(stage => <th key={stage.key}>{stage.title}</th>)}<th /></tr></thead><tbody>
            {views.map(view => {
              const r = rowOf(view);
              return <Fragment key={view.pipeline.id}><tr>
                <th scope="row">{nameOf(view)}<small>{r.meta}</small></th>
                {r.cells.map((cell, index) => <td key={index}><div className={`stage-cell ${cell.status}`} title={cell.text}>{cell.status === "running" && <span className="stage-spinner" aria-hidden="true" />}{cell.text}</div></td>)}
                <td className="stage-actions">{actions(view, r)}</td>
              </tr>
              {r.open && view.run && <tr className="stage-detail"><td colSpan={STAGES.length + 2}><TrainingCharts runId={view.run.id} total={r.epochs} live={view.run.state === "TRAINING"} /></td></tr>}
              </Fragment>;
            })}
          </tbody></table></div>
          {/* 휴대폰: 파이프라인별 카드에 단계를 세로로 나열해 글자가 잘리지 않게 한다. */}
          <div className="stage-cards">{views.map(view => {
            const r = rowOf(view);
            return <article key={view.pipeline.id} className="stage-card">
              <header>{nameOf(view)}<small>{r.meta}</small></header>
              <ol>{r.cells.map((cell, index) => <li key={index} className={`stage-card-row ${cell.status}`}>
                <span className="stage-card-name">{STAGES[index].title}</span>
                <span className={`stage-cell ${cell.status}`}>{cell.status === "running" && <span className="stage-spinner" aria-hidden="true" />}{cell.text}</span>
              </li>)}</ol>
              <div className="stage-actions">{actions(view, r)}</div>
              {r.open && view.run && <TrainingCharts runId={view.run.id} total={r.epochs} live={view.run.state === "TRAINING"} />}
            </article>;
          })}</div>
          </>}
        <p className="muted">평가 기준: 후보의 mAP50–95가 비교 모델 이상이어야 배포할 수 있습니다. 배포는 장치 정책(고정·자동)을 따르며 IoT가 heartbeat로 받아 적용합니다.</p>
      </section>
      <p className="muted pipeline-links">세부 화면: <Link href="/ai-training/collect">수집 이미지</Link> · <Link href="/ai-training/label">자동 라벨링 결과</Link> · <Link href="/ai-training/train">학습 실행 이력</Link> · <Link href="/ai-training/deploy">배포 이력</Link></p>
      {deploying && <div className="camera-backdrop" role="dialog" aria-modal="true" aria-label="모델 배포">
        <div className="pipeline-dialog"><h2>모델 배포</h2><p className="muted">{deploying.pipeline.name} · SHA {deploying.release?.sha256.slice(0, 12)}</p>
          {devices.length === 0 ? <p>등록된 IoT 장치가 없습니다.</p> : <label>대상 장치 <select value={device} onChange={e => setDevice(e.target.value)}>{devices.map(d => <option key={d.deviceId} value={d.deviceId}>{d.name} ({d.deviceId}) · {d.online ? "온라인" : "오프라인"} · {(policies[d.deviceId]?.mode ?? "PINNED") === "PINNED" ? "고정" : "자동"}</option>)}</select></label>}
          {device && (policies[device]?.mode ?? "PINNED") === "PINNED" && <p className="muted">고정 장치입니다. 배포하면 이 장치의 고정 모델이 이 모델로 바뀝니다. 정책은 <Link href="/ai-training/deploy">배포 현황판</Link>에서 관리합니다.</p>}
          <div className="training-actions"><button className="primary" disabled={!device} onClick={() => void deploy()}>{device && (policies[device]?.mode ?? "PINNED") === "PINNED" ? "이 모델로 고정 배포" : "배포 요청"}</button><button onClick={() => setDeploying(null)}>닫기</button></div>
        </div></div>}
    </main>
  </div>;
}
