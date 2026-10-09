"use client";

import Image from "next/image";
import Link from "next/link";
import ImageCarousel from "./image-carousel";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Device } from "../../lib/api";
import SideNav from "./side-nav";
import UploadPanel from "./upload-panel";

type Analysis = { answer: string; labels: string[] };
type Sample = { id: string; title: string; width: number; height: number; deviceId: string | null; cameraId: string | null; capturedAt: string | null; createdAt: string; status: string; revision: number; proposal: string | null; annotation: string | null; source: string; importId: string | null };
type Page = { items: Sample[]; total: number; page: number; size: number };
type Stats = { total: number; unprocessed: number; pendingReview: number; approved: number; labeled: number; excluded: number; queued: number; running: number; lastReceivedAt: string | null };
type Job = { id: string; sampleId: string; instruction: string; model: string; status: string; error: string | null; createdAt: string; result: string | null };
const names: Record<string, string> = { LABELED: "라벨링 완료", NEW: "처리 대기", REVIEW: "결과 확인 대기", APPROVED: "확인 완료", EXCLUDED: "제외", QUEUED: "대기", RUNNING: "실행 중", COMPLETED: "완료", FAILED: "실패" };
const sources: Record<string, string> = { UPLOAD: "직접 업로드", VIDEO_FILE: "영상 파일", YOUTUBE: "유튜브" };
const origin = (s: Sample) => s.deviceId ? `${s.deviceId} · ${s.cameraId || "카메라 정보 없음"}` : sources[s.source] ?? "장치 정보 없음";
const date = (value: string | null) => value ? new Date(value).toLocaleString("ko-KR") : "—";
function resultOf(value: string | null): Analysis | null {
  if (!value) return null;
  try { const parsed = JSON.parse(value); const result = parsed.result ?? parsed; return typeof result.answer === "string" && Array.isArray(result.labels) ? result : null; } catch { return null; }
}
async function post<T>(path: string, body: object) {
  const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
  return api<T>(path, { method: "POST", headers: { [csrf.headerName]: csrf.token, "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

type SizeFacet = { deviceId: string; cameraId: string | null; width: number; height: number; count: number; firstAt: string; lastAt: string };
const aspect = (w: number, h: number) => Math.abs(w / h - 16 / 9) < 0.02 ? "16:9" : Math.abs(w / h - 4 / 3) < 0.02 ? "4:3" : `${(w / h).toFixed(2)}:1`;
const shortDay = (value: string) => new Date(value).toLocaleDateString("ko-KR", { month: "2-digit", day: "2-digit" });

export default function CollectionPage({ initialStatus = "", title = "수집 데이터 현황", stage = "" }: { initialStatus?: string; title?: string; stage?: string }) {
  const router = useRouter();
  const [data, setData] = useState<Page>({ items: [], total: 0, page: 0, size: 40 });
  const [approved, setApproved] = useState<Page>({ items: [], total: 0, page: 0, size: 40 });
  const [approvedPage, setApprovedPage] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [page, setPage] = useState(0);
  const [device, setDevice] = useState("");
  // 카메라·이미지 크기 필터("usb-0|768x576"). 같은 usb-0이라도 웹캠을 바꾸면 크기가 달라진다.
  const [variant, setVariant] = useState("");
  const [sizes, setSizes] = useState<SizeFacet[]>([]);
  const [status, setStatus] = useState(initialStatus);
  const [preview, setPreview] = useState<Sample | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const previewId = preview?.id;
  const fail = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) router.replace("/login");
    else if (e instanceof ApiError && e.status === 409) setError("진행 중인 작업이 있거나 선택한 이미지 상태가 변경되었습니다. 새로고침 후 다시 시도하세요.");
    else setError("서버에서 정보를 받지 못했습니다. 연결 상태를 확인하세요.");
  }, [router]);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ page: String(page) });
    if (device) query.set("deviceId", device);
    if (status) query.set("status", status);
    const [variantCamera, variantSize] = variant ? variant.split("|") : ["", ""];
    const variantQuery = variant ? `&cameraId=${encodeURIComponent(variantCamera)}&size=${variantSize}` : "";
    if (variant) { query.set("cameraId", variantCamera); query.set("size", variantSize); }
    const [images, counts, recentJobs, deviceList, reviewed, sizeList] = await Promise.all([
      api<Page>(`/training/samples?${query}`, { signal }), api<Stats>("/training/stats", { signal }),
      api<Job[]>("/training/label-jobs", { signal }), api<Device[]>("/devices", { signal }),
      api<Page>(`/training/samples?status=LABELED&page=${approvedPage}${device ? `&deviceId=${encodeURIComponent(device)}` : ""}${variantQuery}`, { signal }),
      // 크기 집계는 보조 정보라 실패해도 목록 갱신을 막지 않는다.
      device ? api<SizeFacet[]>(`/training/samples/sizes?deviceId=${encodeURIComponent(device)}`, { signal }).catch(() => []) : Promise.resolve([]),
    ]);
    if (signal?.aborted) return;
    setApproved(reviewed); setData(images); setStats(counts); setJobs(recentJobs); setDevices(deviceList); setSizes(sizeList); setUpdatedAt(new Date().toISOString());
  }, [page, device, status, approvedPage, variant]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(controller.signal); if (!controller.signal.aborted) setError(""); }
      catch (e) { if (!controller.signal.aborted) fail(e); }
      finally { if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(poll, 5000); } }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh, fail]);
  useEffect(() => {
    if (!previewId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const [sample, history] = await Promise.all([api<Sample>(`/training/samples/${previewId}`, { signal: controller.signal }), api<Job[]>(`/training/label-jobs?sampleId=${previewId}`, { signal: controller.signal })]);
        if (!controller.signal.aborted) { setPreview(sample); setHistory(history); }
      } catch (e) { if (!controller.signal.aborted) fail(e); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [previewId, fail]);
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await fn(); await refresh(); } catch (e) { fail(e); } finally { setBusy(false); }
  }
  function open(sample: Sample) { setHistory([]); setPreview(sample); window.setTimeout(() => document.querySelector('[aria-label="이미지 상세"]')?.scrollIntoView({ behavior: "smooth", block: "start" }), 0); }
  const analysis = resultOf(preview?.proposal ?? null);
  const previewBusy = !history.length || history.some(j => ["QUEUED", "RUNNING"].includes(j.status));
  return <div className="app-shell control-shell">
    <SideNav active={stage ? "ai-training" : "training-data"} />
    <main className="training-main control-room collection-control">
      <Link className="training-back" href="/ai-training">← 뒤로가기 · AI 학습</Link>
      <header className="collection-heading"><div><p className="eyebrow">이미지 처리 현황</p><h1>{title}</h1><p className="muted">수집 즉시 Gemma가 이미지 설명과 태그를 생성합니다. 결과는 자동으로 저장됩니다.</p></div><button disabled={busy} onClick={() => void action(refresh)}>새로고침</button></header>
      {error && <p className="training-error" role="alert">{error}</p>}{message && <p className="training-success" role="status">{message}</p>}
      <section className="collection-metrics" aria-label="전체 수집 현황">
        {[["전체 이미지", stats?.total], ["처리 대기", stats?.unprocessed], ["처리 중", stats?.running], ["라벨링 완료", stats?.labeled]].map(([name, count]) => <article key={name}><span>{name}</span><strong>{count ?? "—"}</strong></article>)}
      </section><p className="muted">전체 작업 대기 {stats?.queued ?? "—"}건 · 실행 중 {stats?.running ?? "—"}건 · 제외 {stats?.excluded ?? "—"}장 · 마지막 수신 {date(stats?.lastReceivedAt ?? null)}</p>
      {stage !== "label" && <UploadPanel onUploaded={() => void refresh().catch(fail)} />}
      <section className="training-card"><div className="collection-heading"><div><h2>수집된 이미지</h2><p className="muted">조회 결과 {data.total}장 · 최근 갱신 {date(updatedAt)}</p></div><div className="training-actions">
        <label>장치 <select value={device} onChange={e => { setDevice(e.target.value); setVariant(""); setPage(0); setApprovedPage(0);  }}><option value="">전체 장치</option>{devices.map(d => <option key={d.deviceId} value={d.deviceId}>{d.name}</option>)}</select></label>
        {device && sizes.length > 0 && <label>카메라·크기 <select value={variant} onChange={e => { setVariant(e.target.value); setPage(0); setApprovedPage(0); }}><option value="">전체</option>{sizes.map(f => { const key = `${f.cameraId ?? ""}|${f.width}x${f.height}`; return <option key={key} value={key}>{f.cameraId || "카메라 없음"} · {f.width}×{f.height} ({aspect(f.width, f.height)}) · {f.count}장 · {shortDay(f.firstAt)}~{shortDay(f.lastAt)}</option>; })}</select></label>}
        <label>상태 <select value={status} onChange={e => { setStatus(e.target.value); setPage(0);  }}><option value="">전체 상태</option>{["NEW", "LABELED", "EXCLUDED"].map(value => <option key={value} value={value}>{names[value]}</option>)}</select></label>

      </div></div>
      {data.items.length === 0 ? <div className="collection-empty"><strong>{loading ? "수집 현황을 불러오는 중입니다." : data.total === 0 && !device && !status ? "아직 수신한 이미지가 없습니다." : "조건에 맞는 이미지가 없습니다."}</strong><p className="muted">IoT 장치가 이미지를 전송하거나 직접 올리면 여기에 쌓입니다.</p></div> : <ImageCarousel key={`${page}-${device}-${status}`} label="수집된 이미지">{data.items.map(sample => <article className="collection-tile" key={sample.id}>
        <button className="collection-thumbnail" onClick={() => open(sample)} aria-label={`${sample.title} 상세 보기`}><Image unoptimized src={`/api/training/samples/${sample.id}/image`} alt={sample.title} width={sample.width} height={sample.height} sizes="(max-width:760px) 90vw, 25vw" /></button>
        <div className="collection-tile-body"><strong>{sample.title}</strong><span className="collection-badge">{names[sample.status]}</span><p className="muted">{origin(sample)}<br />촬영 {date(sample.capturedAt)}<br />수신 {date(sample.createdAt)}</p></div>
      </article>)}</ImageCarousel>}
      <div className="training-actions"><button disabled={!page || busy} onClick={() => { setPage(page - 1);  }}>이전</button><span>{page + 1} / {Math.max(1, Math.ceil(data.total / data.size))}</span><button disabled={(page + 1) * data.size >= data.total || busy} onClick={() => { setPage(page + 1);  }}>다음</button></div>
      </section>
      <section className="training-card"><div className="collection-heading"><div><h2>라벨링 완료 이미지</h2><p className="muted">{approved.total}장 · 좌우로 넘겨 확인하세요.</p></div></div>
        {approved.items.length ? <ImageCarousel key={`${approvedPage}-${device}`} label="라벨링 완료 이미지">{approved.items.map(sample => <article className="collection-tile" key={sample.id}>
          <button className="collection-thumbnail" onClick={() => open(sample)} aria-label={`${sample.title} 라벨링 결과 보기`}><Image unoptimized src={`/api/training/samples/${sample.id}/image`} alt={sample.title} width={sample.width} height={sample.height} sizes="320px" /></button>
          <div className="collection-tile-body"><strong>{sample.title}</strong><p className="muted">{origin(sample)} · {date(sample.capturedAt ?? sample.createdAt)}</p><span className="collection-badge">라벨링 완료</span></div>
        </article>)}</ImageCarousel> : <p className="muted">라벨링 완료 이미지가 없습니다.</p>}
        <div className="training-actions"><button disabled={!approvedPage} onClick={() => setApprovedPage(approvedPage - 1)}>이전 페이지</button><span>{approvedPage + 1} / {Math.max(1, Math.ceil(approved.total / approved.size))}</span><button disabled={(approvedPage + 1) * approved.size >= approved.total} onClick={() => setApprovedPage(approvedPage + 1)}>다음 페이지</button></div>
      </section>
      {preview && <section className="training-card" aria-label="이미지 상세"><div className="collection-heading"><h2>{preview.title}</h2><button onClick={() => setPreview(null)}>상세 닫기</button></div><div className="collection-detail"><div><div className="label-image"><Image className="collection-full-image" unoptimized src={`/api/training/samples/${preview.id}/image`} alt={preview.title} width={preview.width} height={preview.height} /></div><p className="muted">{preview.width} × {preview.height} · {origin(preview)}<br />촬영 {date(preview.capturedAt)} · 수신 {date(preview.createdAt)}</p></div><div><h2>최근 분석 결과</h2>{analysis ? <><p className="analysis-answer">{analysis.answer}</p><div className="training-actions">{analysis.labels.map((label, i) => <span className="collection-badge" key={i}>{label}</span>)}</div></> : <p className="muted">자동 라벨링 결과를 기다리고 있습니다. 작업 이력에서 진행 상태를 확인하세요.</p>}
        {preview.status === "NEW" && history.some(j => j.status === "FAILED") && <button disabled={busy || previewBusy} onClick={() => void action(async () => {
          await post(`/training/samples/${preview.id}/retry-label`, {});
          setHistory(await api<Job[]>(`/training/label-jobs?sampleId=${preview.id}`));
          setMessage("자동 라벨링을 다시 등록했습니다.");
        })}>자동 라벨링 재시도</button>}
        <h2>이 이미지의 작업 이력</h2>{history.length === 0 ? <p className="muted">실행한 작업이 없습니다.</p> : history.map(job => <details key={job.id} className="job-detail"><summary>{date(job.createdAt)} · {names[job.status]}</summary><p className="analysis-answer">{resultOf(job.result)?.answer || job.error || "작업 결과를 기다리는 중입니다."}</p></details>)}
      </div></div></section>}
      <details className="training-card" id="job-history"><summary>최근 작업 이력</summary><h2>최근 작업 이력</h2><p className="muted">최근 40개 · 5초마다 갱신</p>{jobs.length === 0 ? <p>실행한 작업이 없습니다.</p> : <div className="table-scroll"><table><thead><tr><th>요청 시각</th><th>작업</th><th>상태</th><th>결과</th></tr></thead><tbody>{jobs.map(job => <tr key={job.id}><td>{date(job.createdAt)}</td><td className="prompt-cell">자동 라벨링</td><td>{names[job.status]}</td><td><button onClick={() => void action(async () => open(await api<Sample>(`/training/samples/${job.sampleId}`)))}>이미지·결과 보기</button>{job.error && <p role="status">{job.error}</p>}</td></tr>)}</tbody></table></div>}</details>
    </main>
  </div>;
}
