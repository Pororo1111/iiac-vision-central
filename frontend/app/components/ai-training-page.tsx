"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../lib/api";
import SideNav from "./side-nav";
import TrainingRuns from "./training-runs";
import ModelDeployments from "./model-deployments";

type Stats = { total: number; labeled: number; queued: number; running: number; lastReceivedAt: string | null };
type Progress = { available: boolean; step?: string; state?: string; updatedAt?: string; current?: number; total?: number };
const steps = [
  { key: "collect", title: "이미지 수집", description: "현장 이미지가 도착하면 자동으로 작업을 시작합니다.", icon: "01", input: "현장 카메라", output: "수집 이미지", category: "데이터 수신" },
  { key: "label", title: "자동 라벨링", description: "Gemma가 이미지의 내용과 태그를 정리합니다.", icon: "02", input: "수집 이미지", output: "설명 · 태그", category: "Gemma 분석" },
  { key: "dataset", title: "데이터셋 구성", description: "학습에 사용할 데이터를 준비합니다.", icon: "03", input: "라벨링 결과", output: "학습 데이터", category: "데이터 준비" },
  { key: "train", title: "모델 학습", description: "준비된 데이터로 후보 모델을 학습합니다.", icon: "04", input: "학습 데이터", output: "후보 모델", category: "모델 생성" },
  { key: "deploy", title: "모델 배포", description: "장치별 배포 정책(고정·자동)과 목표·실행 중 모델, 배포 결과를 관리합니다.", icon: "05", input: "후보 모델", output: "현장 장치", category: "모델 전달" },
];
export default function AiTrainingPage({ detailStep }: { detailStep?: string }) {
  const router = useRouter();
  const flowRef = useRef<HTMLElement>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [latestRun, setLatestRun] = useState<{ state: string; summary: string } | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [updated, setUpdated] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const [counts, local, runs] = await Promise.all([api<Stats>("/training/stats", { signal: controller.signal }), api<Progress>("/training/progress", { signal: controller.signal }), api<{ state: string; summary: string }[]>("/training/runs", { signal: controller.signal })]);
        if (!controller.signal.aborted) { setStats(counts); setProgress(local); setLatestRun(runs[0] ?? null); setUpdated(new Date().toLocaleTimeString("ko-KR")); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (cause instanceof ApiError && cause.status === 401) router.replace("/login");
          else setError("연결 확인 필요 · 마지막 수신 상태를 표시합니다.");
        }
      } finally { if (!controller.signal.aborted) timer = setTimeout(poll, 5000); }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [router]);
  function state(key: string) {
    if (key === "deploy") return "배포 관리";
    if (!stats) return "연결 대기";
    if (key === "collect") return stats.lastReceivedAt && Date.now() - Date.parse(stats.lastReceivedAt) < 30000 ? "수신 중" : "수신 대기";
    if (key === "label") return stats.running ? `${stats.running}건 처리 중` : stats.queued ? "처리 대기" : "대기";
    if (latestRun && (key === "dataset" || key === "train")) {
      if (latestRun.state === "FAILED") return "최근 실행 실패";
      if (key === "dataset") return latestRun.state === "PREPARING" ? "박스 생성·선별 중" : "데이터셋 준비 완료";
      return ({ PREPARING: "데이터셋 준비 중", DATASET_READY: "학습 시작 가능", TRAINING: "학습 중", TRAINED: "평가 대기", EVALUATING: "평가 중", EVALUATED: "평가 완료" } as Record<string, string>)[latestRun.state] ?? "상태 확인 필요";
    }
    if (progress?.available && progress.step === key) {
      if (progress.state === "RUNNING") return progress.updatedAt && Date.now() - Date.parse(progress.updatedAt) < 20000 ? "진행 중" : "상태 확인 필요";
      if (progress.state === "FAILED") return "실패";
      return "최근 실행 완료";
    }
    return "준비 전";
  }
  const current = steps.find(step => step.key === detailStep);
  return <div className="app-shell control-shell"><SideNav active="ai-training" /><main className="training-main control-room">
    {current && <Link className="training-back" href="/ai-training">← 뒤로가기 · AI 학습</Link>}
    <header className="control-header"><div><p className="eyebrow">중앙 AI 관제</p><h1>{current?.title ?? "AI 학습"}</h1><p>수집되는 순간부터, 자동으로 정리되는 현장 데이터.</p></div><div className="control-connection"><span className={error ? "control-dot offline" : "control-dot"} />{error ? "연결 확인" : updated ? "시스템 연결됨" : "연결 중"}<small>{updated ? `${updated} 갱신` : "상태 수신 대기"}</small></div></header>
    {error && <p role="alert" className="training-error">{error}</p>}
    <section className="control-metrics" aria-label="처리 현황">{[["수집 이미지", stats?.total, "장"], ["처리 대기", stats?.queued, "건"], ["처리 중", stats?.running, "건"], ["라벨링 완료", stats?.labeled, "장"]].map(([label, value, unit]) => <article key={label}><span>{label}</span><strong>{value ?? "—"}<small>{unit}</small></strong></article>)}</section>
    {current ? <section className="control-panel"><p className="eyebrow">{state(current.key)}</p><h2>{current.title}</h2><p>{current.description}</p>{current.key === "deploy" ? <><p><Link href="/models">모델 현황판</Link>에서 3D 배포 관제과 모델별 고정을 한눈에 관리할 수 있습니다.</p><ModelDeployments /></> : <TrainingRuns step={current.key} />}</section> : <section className="blueprint-panel" aria-labelledby="flow-title">
      <div className="blueprint-toolbar">
        <div><p className="eyebrow">학습 워크플로</p><h2 id="flow-title">현장 데이터에서 모델 배포까지</h2></div>
        <div className="blueprint-controls"><span>5개 단계</span><button type="button" aria-label="이전 단계 방향으로 이동" onClick={() => flowRef.current?.scrollBy({ left: -368, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" })}>←</button><button type="button" aria-label="다음 단계 방향으로 이동" onClick={() => flowRef.current?.scrollBy({ left: 368, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" })}>→</button></div>
      </div>
      <p className="blueprint-help" id="flow-help">좌우로 이동하며 전체 흐름을 살펴보세요. 노드를 선택하면 단계별 상세 화면이 열립니다.</p>
      <nav ref={flowRef} className="blueprint-viewport" aria-label="학습 단계" aria-describedby="flow-help" tabIndex={0}>
        <ol className="blueprint-flow">
          {steps.map((step, index) => <li key={step.key} className={`blueprint-item node-${step.key}`}>
            {index < steps.length - 1 && <svg className="blueprint-wire" viewBox="0 0 88 100" aria-hidden="true"><path d="M 0 58 C 44 58, 44 16, 88 16" /></svg>}
            <Link className="blueprint-node" href={`/ai-training/${step.key}`}>
              <div className="blueprint-node-header"><span className="node-number">{step.icon}</span><span>{step.category}</span><span aria-hidden="true">↗</span></div>
              <div className="blueprint-node-body"><span className="control-status">{state(step.key)}</span><h3>{step.title}</h3><p>{step.description}</p></div>
              <div className="node-ports"><span><i aria-hidden="true" />{step.input}</span><span>{step.output}<i aria-hidden="true" /></span></div>
              <div className="node-link">단계 살펴보기 <span aria-hidden="true">→</span></div>
            </Link>
          </li>)}
        </ol>
      </nav>
      <div className="blueprint-legend"><span><i className="legend-dot automatic" /> 수집 · 라벨링 자동 처리</span><span><i className="legend-dot planned" /> 데이터셋 선별 · 후보 학습</span><span><i className="legend-dot deployment" /> 후보 등록 · 배포 요청</span></div>
    </section>}

    <p className="control-note">5초마다 상태 갱신 · 마지막 이미지 수신 {stats?.lastReceivedAt ? new Date(stats.lastReceivedAt).toLocaleString("ko-KR") : "대기 중"}</p>
  </main></div>;
}
