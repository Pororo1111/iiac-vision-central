"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, csrfJson } from "../../lib/api";

type Import = { id: string; kind: "VIDEO_FILE" | "YOUTUBE"; source: string; title: string; intervalSeconds: number; maxFrames: number; status: string; frames: number; error: string | null; createdAt: string; finishedAt: string | null };
type Mode = "images" | "video" | "youtube";
const statusName: Record<string, string> = { QUEUED: "대기", RUNNING: "프레임 추출 중", COMPLETED: "완료", FAILED: "실패" };
const errors: Record<string, string> = {
  FILE_TOO_LARGE: "파일이 너무 큽니다. 이미지는 장당 10MB, 영상은 500MB까지 올릴 수 있습니다.",
  INVALID_IMAGE: "JPEG·PNG 이미지만 올릴 수 있습니다.", INVALID_IMAGE_SIZE: "이미지는 장당 10MB까지 올릴 수 있습니다.",
  INVALID_FILE_COUNT: "이미지는 한 번에 1~20장까지 올릴 수 있습니다.", UNSUPPORTED_VIDEO: "MP4·MOV·M4V·WebM·MKV·AVI 영상만 올릴 수 있습니다.",
  INVALID_VIDEO_SIZE: "영상은 500MB까지 올릴 수 있습니다.", INVALID_YOUTUBE_URL: "유튜브 영상 주소(watch·youtu.be·shorts)를 입력하세요.",
  INVALID_IMPORT_OPTIONS: "추출 간격은 0.5~60초, 최대 프레임은 1~200장입니다.",
};
const date = (value: string | null) => value ? new Date(value).toLocaleString("ko-KR") : "—";

async function upload<T>(path: string, form: FormData) {
  const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
  return api<T>(path, { method: "POST", headers: { [csrf.headerName]: csrf.token }, body: form });
}

export type UploadResult = { sampleIds: string[]; importIds: string[] };
/** onResult가 있으면 올린 샘플·가져오기 ID를 알려 준다(파이프라인 마법사에서 사용). */
export default function UploadPanel({ onUploaded, onResult }: { onUploaded: () => void; onResult?: (result: UploadResult) => void }) {
  const [mode, setMode] = useState<Mode>("images");
  const [images, setImages] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [gap, setGap] = useState(2);
  const [maxFrames, setMaxFrames] = useState(60);
  const [imports, setImports] = useState<Import[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const list = await api<Import[]>("/training/imports", { signal });
    if (!signal?.aborted) setImports(list);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(controller.signal); } catch { /* 수집 화면의 오류 표시를 따른다. */ }
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);
  async function submit() {
    setBusy(true); setError(""); setMessage("");
    try {
      if (mode === "images") {
        const form = new FormData();
        images.forEach(file => form.append("files", file));
        const saved = await upload<{ id: string }[]>("/training/uploads", form);
        onResult?.({ sampleIds: saved.map(sample => sample.id), importIds: [] });
        setImages([]); setMessage(`이미지 ${saved.length}장을 올렸습니다. 자동 라벨링 대기열에 등록되었습니다.`);
      } else {
        const options = { title, intervalSeconds: gap, maxFrames };
        if (mode === "video" && video) {
          const form = new FormData();
          form.append("file", video); form.append("title", title);
          form.append("intervalSeconds", String(gap)); form.append("maxFrames", String(maxFrames));
          const created = await upload<Import>("/training/imports/video", form);
          onResult?.({ sampleIds: [], importIds: [created.id] });
          setVideo(null);
        } else {
          const created = await csrfJson<Import>("/training/imports/youtube", { ...options, url });
          onResult?.({ sampleIds: [], importIds: [created.id] });
        }
        setUrl(""); setTitle("");
        setMessage("영상 가져오기를 등록했습니다. AI 워커가 프레임을 추출하면 수집 이미지에 추가됩니다.");
        await refresh();
      }
      onUploaded();
    } catch (e) {
      setError(e instanceof ApiError ? errors[e.message] ?? (e.status === 401 ? "로그인이 만료되었습니다." : "업로드하지 못했습니다. 입력값과 연결 상태를 확인하세요.") : "업로드하지 못했습니다.");
    } finally { setBusy(false); }
  }
  const ready = mode === "images" ? images.length > 0 && images.length <= 20 : mode === "video" ? !!video : url.trim().length > 0;
  return <section className="training-card" aria-label="직접 올리기">
    <div className="collection-heading"><div><h2>직접 올리기</h2><p className="muted">이미지·영상 파일·유튜브 링크를 올리면 IoT 수집 이미지와 같은 자동 라벨링을 거칩니다. 올리지 않아도 IoT 수집과 자동 학습 트리거는 그대로 동작합니다.</p></div></div>
    <div className="training-actions" role="tablist">
      {([["images", "이미지"], ["video", "영상 파일"], ["youtube", "유튜브 링크"]] as [Mode, string][]).map(([value, name]) =>
        <button key={value} role="tab" aria-selected={mode === value} className={mode === value ? "primary" : ""} onClick={() => { setMode(value); setError(""); setMessage(""); }}>{name}</button>)}
    </div>
    {error && <p className="training-error" role="alert">{error}</p>}{message && <p className="training-success" role="status">{message}</p>}
    {mode === "images" && <label>JPEG·PNG, 한 번에 최대 20장(장당 10MB) <input type="file" accept="image/jpeg,image/png" multiple onChange={e => setImages(Array.from(e.target.files ?? []))} /></label>}
    {mode === "video" && <label>MP4·MOV·M4V·WebM·MKV·AVI, 최대 500MB <input type="file" accept="video/*,.mkv" onChange={e => setVideo(e.target.files?.[0] ?? null)} /></label>}
    {mode === "youtube" && <label>유튜브 주소 <input type="url" placeholder="https://www.youtube.com/watch?v=..." value={url} maxLength={300} onChange={e => setUrl(e.target.value)} /></label>}
    {mode !== "images" && <div className="training-actions">
      <label>제목(선택) <input value={title} maxLength={120} onChange={e => setTitle(e.target.value)} /></label>
      <label>추출 간격(초) <input type="number" min={0.5} max={60} step={0.5} value={gap} onChange={e => setGap(Number(e.target.value))} /></label>
      <label>최대 프레임 <input type="number" min={1} max={200} value={maxFrames} onChange={e => setMaxFrames(Number(e.target.value))} /></label>
    </div>}
    {mode === "youtube" && <p className="muted">1시간 이하 공개 영상만 720p 이하로 내려받습니다. 저작권·이용 약관상 학습에 써도 되는 영상인지 먼저 확인하세요.</p>}
    <div className="training-actions"><button className="primary" disabled={busy || !ready} onClick={() => void submit()}>{busy ? "올리는 중…" : "올리기"}</button></div>
    <h3>최근 영상 가져오기</h3>
    {imports.length === 0 ? <p className="muted">등록한 영상이 없습니다.</p> : <div className="table-scroll"><table><thead><tr><th>요청 시각</th><th>종류</th><th>제목</th><th>상태</th><th>추출</th></tr></thead><tbody>
      {imports.map(item => <tr key={item.id}><td>{date(item.createdAt)}</td><td>{item.kind === "YOUTUBE" ? "유튜브" : "영상 파일"}</td><td className="prompt-cell">{item.title}</td><td>{statusName[item.status] ?? item.status}{item.error && <p role="status">{item.error}</p>}</td><td>{item.frames} / {item.maxFrames}장 · {item.intervalSeconds}초 간격</td></tr>)}
    </tbody></table></div>}
  </section>;
}
