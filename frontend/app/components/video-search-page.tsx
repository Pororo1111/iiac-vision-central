"use client";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, csrfJson } from "../../lib/api";
import SideNav from "./side-nav";

type State = "present" | "absent" | "unknown";
type Filter = {
  deviceId: string | null;
  cameraId: string | null;
  from: string | null;
  to: string | null;
  helmet: State | null;
  vest: State | null;
  text: string | null;
};
type Person = { trackId: string; helmet: State; vest: State; evidence: string; yoloHelmet: string | null; yoloVest: string | null };
type Segment = {
  batchId: string;
  deviceId: string;
  cameraId: string;
  startedAt: string;
  endedAt: string;
  summary: string;
  persons: Person[];
  matched: string[];
  recordingId: string | null;
  offsetSeconds: number | null;
  playbackPath: string | null;
  thumbnailPath: string | null;
};
type Trace = {
  model: string;
  promptVersion: string;
  context: string[];
  message: string;
  output: Record<string, unknown>;
  range: { from: string | null; to: string | null };
  promptTokens: number | null;
  outputTokens: number | null;
  elapsedMs: number;
};
type Question = {
  id: string;
  conversationId: string;
  seq: number;
  message: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELED";
  filter: Filter | null;
  reply: string | null;
  error: string | null;
  elapsedMs: number | null;
  createdAt: string;
  trace: Trace | null;
  results: Segment[] | null;
};
type Stage = "idle" | "queued" | "interpret" | "search" | "done" | "failed";
type ConversationSummary = { id: string; title: string; questions: number; createdAt: string; updatedAt: string };

const EXAMPLES = ["오늘 안전모 안 쓴 사람 나온 장면 찾아줘", "그중에 조끼는 입은 사람만", "모니터 앞에 앉아 있는 장면 있어?"];
const helmetNames: Record<State, string> = { present: "안전모 착용", absent: "안전모 미착용", unknown: "안전모 불명" };
const vestNames: Record<State, string> = { present: "조끼 착용", absent: "조끼 미착용", unknown: "조끼 불명" };

function clock(value: string) {
  return new Date(value).toLocaleTimeString("ko-KR", {
    timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}
function stamp(value: string) {
  return new Date(value).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}
function conditions(f: Filter) {
  const chips = [];
  if (f.from && f.to) chips.push(`${stamp(f.from)} ~ ${stamp(f.to)}`);
  else if (f.from) chips.push(`${stamp(f.from)} 이후`);
  else if (f.to) chips.push(`${stamp(f.to)} 이전`);
  else chips.push("전체 기간");
  if (f.deviceId) chips.push(`장치 ${f.deviceId}`);
  if (f.cameraId) chips.push(`카메라 ${f.cameraId}`);
  if (f.helmet) chips.push(helmetNames[f.helmet]);
  if (f.vest) chips.push(vestNames[f.vest]);
  if (f.text) chips.push(`“${f.text}” 포함`);
  return chips;
}

/** 조건에 맞은 사람의 판정 근거. YOLO 연결 규칙은 present만 만들 수 있으므로 absent 조건은 항상 Gemma 판정이다. */
function verdict(s: Segment, f: Filter | null) {
  if (!f || (!f.helmet && !f.vest) || !s.matched.length) return null;
  const people = s.persons.filter((p) => s.matched.includes(p.trackId));
  const agrees = people.some(
    (p) => (!f.helmet || p.yoloHelmet === p.helmet) && (!f.vest || p.yoloVest === p.vest),
  );
  return { agrees, people };
}

function Flow({ stage, playing }: { stage: Stage; playing: boolean }) {
  const on = (...stages: Stage[]) => stages.includes(stage);
  const nodes = [
    { id: "query", x: 20, y: 120, title: "질문", sub: "관리자 입력", active: on("queued"), done: on("interpret", "search", "done") },
    { id: "agent", x: 170, y: 120, title: "질문 해석", sub: "Gemma 4 12B", note: "조건 JSON", active: on("interpret"), done: on("search", "done") },
    { id: "segments", x: 330, y: 20, title: "구간 판정", sub: "YOLO 추적 + Gemma 4", note: "10초 구간", active: on("search"), done: on("done") },
    { id: "search", x: 330, y: 120, title: "조건 검색", sub: "PostgreSQL", note: "보호구·기간·문구", active: on("search"), done: on("done") },
    { id: "video", x: 330, y: 220, title: "원본 녹화", sub: "MP4 구간 재생", note: "Range 요청", active: playing, done: false },
    { id: "output", x: 490, y: 120, title: "결과", sub: "구간·재생 위치", active: on("done") || playing, done: false },
  ];
  const edges: [string, string, boolean][] = [
    ["M130 150 L170 150", "query", on("queued", "interpret")],
    ["M290 150 L330 150", "agent", on("search")],
    ["M390 80 L390 120", "segments", on("search")],
    ["M450 150 L490 150", "search", on("search", "done")],
    ["M450 250 C470 250 470 170 490 170", "video", playing],
  ];
  return (
    <svg className="vsx-flow-svg" viewBox="0 0 620 290" role="img" aria-label="영상 검색 처리 흐름">
      {edges.map(([d, id, active]) => (
        <path key={id} d={d} className={`vsx-edge${active ? " active" : ""}`} />
      ))}
      {nodes.map((n) => (
        <g key={n.id} className={`vsx-node${n.active ? " active" : ""}${n.done ? " done" : ""} n-${n.id}`}>
          <rect x={n.x} y={n.y} width={n.id === "query" ? 110 : 120} height="60" rx="8" />
          <text x={n.x + (n.id === "query" ? 55 : 60)} y={n.y + 24} className="t">{n.title}</text>
          <text x={n.x + (n.id === "query" ? 55 : 60)} y={n.y + 40} className="s">{n.sub}</text>
          {n.note && <text x={n.x + 60} y={n.y + 53} className="n">{n.note}</text>}
        </g>
      ))}
    </svg>
  );
}

export default function VideoSearchPage() {
  const router = useRouter();
  const [questions, setQuestions] = useState<Question[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [manual, setManual] = useState<{ filter: Filter; results: Segment[] } | null>(null);
  const [search, setSearch] = useState({ text: "", helmet: "", vest: "" });
  const [playing, setPlaying] = useState<{ segment: Segment; key: number } | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [now, setNow] = useState(() => Date.now());
  // 지난 대화 목록. null이면 닫힘.
  const [history, setHistory] = useState<ConversationSummary[] | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const conversationId = questions[0]?.conversationId ?? null;
  const pending = questions.find((q) => q.status === "QUEUED" || q.status === "RUNNING");
  const last = questions.at(-1);

  function fail(e: unknown, fallback: string) {
    if (e instanceof ApiError && e.status === 401) router.replace("/login");
    else if (e instanceof ApiError && e.status === 409) setError("앞 질문의 답을 기다리는 중입니다.");
    else setError(fallback);
  }

  // 답을 기다리는 질문만 1초마다 확인한다.
  useEffect(() => {
    if (!pending) return;
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const q = await api<Question>(`/search/questions/${pending.id}`);
        if (!active) return;
        setQuestions((list) => list.map((item) => (item.id === q.id ? q : item)));
        setNow(Date.now());
        if (q.status === "COMPLETED") {
          setSelected(q.id);
          setManual(null);
        }
      } catch (e) {
        if (active) fail(e, "답을 확인하지 못했습니다. 잠시 뒤 다시 확인합니다.");
      }
    }, 1000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending?.id, questions]);

  // 흐름도 강조: 마지막 질문 상태를 따른다. 검색은 서버에서 즉시 끝나므로 잠시 강조한 뒤 완료로 둔다.
  useEffect(() => {
    if (!last) return setStage("idle");
    if (last.status === "QUEUED") return setStage("queued");
    if (last.status === "RUNNING") return setStage("interpret");
    if (last.status !== "COMPLETED") return setStage("failed");
    setStage("search");
    const timer = setTimeout(() => setStage("done"), 1400);
    return () => clearTimeout(timer);
  }, [last?.id, last?.status]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [questions.length, last?.status]);

  const shown = useMemo(() => {
    if (manual) return { title: "직접 검색", filter: manual.filter, results: manual.results };
    const q = questions.find((item) => item.id === selected && item.status === "COMPLETED");
    return q ? { title: `질문 ${q.seq}`, filter: q.filter, results: q.results ?? [] } : null;
  }, [manual, questions, selected]);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || pending || sending) return;
    setSending(true);
    setError("");
    try {
      const q = await csrfJson<Question>("/search/questions", { conversationId, message: trimmed });
      setQuestions((list) => [...list, q]);
      setMessage("");
    } catch (e) {
      fail(e, "질문을 보내지 못했습니다.");
    } finally {
      setSending(false);
    }
  }

  async function stop() {
    if (!pending) return;
    try {
      const q = await csrfJson<Question>(`/search/questions/${pending.id}/cancel`);
      setQuestions((list) => list.map((item) => (item.id === q.id ? q : item)));
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) fail(e, "생성을 중지하지 못했습니다.");
    }
  }

  async function directSearch() {
    const params = new URLSearchParams();
    if (search.text.trim()) params.set("text", search.text.trim());
    if (search.helmet) params.set("helmet", search.helmet);
    if (search.vest) params.set("vest", search.vest);
    try {
      const results = await api<Segment[]>(`/observations/search?${params}`);
      setManual({
        filter: { deviceId: null, cameraId: null, from: null, to: null, text: search.text.trim() || null,
          helmet: (search.helmet || null) as State | null, vest: (search.vest || null) as State | null },
        results,
      });
      setError("");
    } catch (e) {
      fail(e, "검색하지 못했습니다.");
    }
  }

  function reset() {
    setQuestions([]);
    setSelected(null);
    setManual(null);
    setError("");
  }

  async function openHistory() {
    if (history) return setHistory(null);
    try {
      setHistory(await api<ConversationSummary[]>("/search/conversations"));
      setError("");
    } catch (e) {
      fail(e, "지난 대화를 불러오지 못했습니다.");
    }
  }

  // 대화 조회는 결과를 싣지 않으므로 완료된 질문은 한 건씩 다시 읽어 현재 데이터로 결과를 받는다.
  async function openConversation(id: string) {
    try {
      const list = await api<Question[]>(`/search/conversations/${id}`);
      const full = await Promise.all(list.map((q) => (q.status === "COMPLETED" ? api<Question>(`/search/questions/${q.id}`) : q)));
      reset();
      setQuestions(full);
      setSelected(full.findLast((q) => q.status === "COMPLETED")?.id ?? null);
      setHistory(null);
    } catch (e) {
      fail(e, "대화를 열지 못했습니다.");
    }
  }

  async function removeConversation(c: ConversationSummary) {
    if (!window.confirm(`"${c.title}" 대화를 삭제할까요?`)) return;
    try {
      const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
      await api(`/search/conversations/${c.id}`, { method: "DELETE", headers: { [csrf.headerName]: csrf.token } });
      setHistory((list) => list?.filter((item) => item.id !== c.id) ?? null);
      if (c.id === conversationId) reset();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setHistory((list) => list?.filter((item) => item.id !== c.id) ?? null);
      else fail(e, "대화를 삭제하지 못했습니다.");
    }
  }

  function addToChat(s: Segment) {
    setMessage(`${clock(s.startedAt)}~${clock(s.endedAt)} 구간에서 `);
    input.current?.focus();
  }

  function seek() {
    if (video.current && playing?.segment.offsetSeconds != null) {
      video.current.currentTime = playing.segment.offsetSeconds;
      void video.current.play().catch(() => undefined);
    }
  }

  return (
    <div className="app-shell control-shell">
      <SideNav active="video-search" />
      <main className="training-main control-room vsx">
      <header className="collection-heading vsx-heading">
        <div>
          <p className="eyebrow">영상 분석</p>
          <h1>영상 검색</h1>
          <p className="muted">질문 → 조건 → 구간 → 원본. 분석된 현장 영상에서 보호구·시간·장면 조건으로 구간을 찾고 해당 시각의 원본을 재생합니다.</p>
        </div>
      </header>
      <div className="vsx-grid">
        <section className="vsx-left" aria-label="원본 영상과 처리 흐름">
          <div className="vsx-player">
            {playing ? (
              <>
                <video key={playing.key} ref={video} controls muted playsInline src={playing.segment.playbackPath ?? undefined} onLoadedMetadata={seek} />
                <div className="vsx-player-bar">
                  <strong>
                    {playing.segment.cameraId} · {clock(playing.segment.startedAt)} – {clock(playing.segment.endedAt)}
                  </strong>
                  <span>녹화 {playing.segment.offsetSeconds?.toFixed(1)}초 위치</span>
                  <button onClick={seek}>구간 처음으로</button>
                </div>
              </>
            ) : (
              <div className="vsx-player-empty">
                <span>▶</span>
                <p>결과 클립을 누르면 해당 시각의 원본 녹화를 재생합니다.</p>
              </div>
            )}
          </div>
          <div className="vsx-flow">
            <div className="vsx-flow-head">
              <span>처리 흐름</span>
              <small>
                {stage === "queued" && "질문 대기"}
                {stage === "interpret" && "Gemma가 질문을 조건으로 바꾸는 중"}
                {stage === "search" && "분석된 구간에서 조건 검색"}
                {stage === "done" && "결과 준비됨"}
                {stage === "failed" && "처리되지 않음"}
                {stage === "idle" && "질문을 기다리는 중"}
              </small>
            </div>
            <Flow stage={stage} playing={!!playing} />
            {playing && (
              <p className="vsx-flow-note">
                {playing.segment.summary}
              </p>
            )}
          </div>
        </section>

        <section className="vsx-results" aria-label="검색 결과">
          <form
            className="vsx-search"
            onSubmit={(e) => {
              e.preventDefault();
              void directSearch();
            }}
          >
            <input value={search.text} maxLength={100} placeholder="장면 설명에서 찾기 (예: 복도)" onChange={(e) => setSearch({ ...search, text: e.target.value })} aria-label="장면 문구" />
            <button>검색</button>
            <select value={search.helmet} onChange={(e) => setSearch({ ...search, helmet: e.target.value })} aria-label="안전모">
              <option value="">안전모 전체</option>
              <option value="absent">미착용</option>
              <option value="present">착용</option>
              <option value="unknown">불명</option>
            </select>
            <select value={search.vest} onChange={(e) => setSearch({ ...search, vest: e.target.value })} aria-label="조끼">
              <option value="">조끼 전체</option>
              <option value="absent">미착용</option>
              <option value="present">착용</option>
              <option value="unknown">불명</option>
            </select>
          </form>
          {shown ? (
            <>
              <div className="vsx-results-head">
                <strong>{shown.title}</strong>
                <span>{shown.results.length}개 구간{shown.results.length === 50 ? " (최신 50개)" : ""}</span>
                <div className="vsx-chips">
                  {shown.filter && conditions(shown.filter).map((c) => <span key={c}>{c}</span>)}
                </div>
              </div>
              {shown.results.length ? (
                <div className="vsx-cards">
                  {shown.results.map((s) => {
                    const v = verdict(s, shown.filter);
                    return (
                      <article key={s.batchId} className={`vsx-card${v ? (v.agrees ? " agree" : " gemma") : ""}${playing?.segment.batchId === s.batchId ? " playing" : ""}`}>
                        <header>
                          <span title={s.deviceId}>{s.cameraId}</span>
                          <button onClick={() => addToChat(s)}>대화에 추가</button>
                        </header>
                        <button className="vsx-thumb" disabled={!s.playbackPath} onClick={() => setPlaying({ segment: s, key: Date.now() })} aria-label={`${clock(s.startedAt)} 구간 재생`}>
                          {s.thumbnailPath ? <img src={s.thumbnailPath} alt="" loading="lazy" /> : <span className="vsx-thumb-empty">대표 이미지 없음</span>}
                          <span className="vsx-play">▶</span>
                          <span className="vsx-time">
                            {clock(s.startedAt)} – {clock(s.endedAt)}
                          </span>
                        </button>
                        {v && (
                          <p className={`vsx-verdict ${v.agrees ? "agree" : "gemma"}`} title="YOLO 연결 규칙은 착용(present)만 만들 수 있어 미착용 조건은 항상 Gemma 판정입니다.">
                            {v.agrees ? "✓ YOLO·Gemma 둘 다 확인" : "? Gemma만 확인 (YOLO 미탐지) — 직접 확인 권장"}
                          </p>
                        )}
                        <div className="vsx-tags">
                          {s.persons.filter((p) => !s.matched.length || s.matched.includes(p.trackId)).slice(0, 4).map((p) => (
                            <span key={p.trackId} className={s.matched.includes(p.trackId) ? "hit" : undefined} title={p.evidence}>
                              {s.matched.includes(p.trackId) ? "✓ " : ""}
                              {p.trackId} {helmetNames[p.helmet]}·{vestNames[p.vest]}
                            </span>
                          ))}
                        </div>
                        <p className="vsx-summary">{s.summary}</p>
                      </article>
                    );
                  })}
                </div>
              ) : (
                <div className="vsx-empty">조건에 맞는 구간이 없습니다.</div>
              )}
            </>
          ) : (
            <div className="vsx-empty">
              <span>▢</span>
              결과가 여기에 표시됩니다.
            </div>
          )}
        </section>

        <section className="vsx-chat" aria-label="영상 에이전트">
          <header className="vsx-chat-head">
            <button className="vsx-history-toggle" disabled={!!pending} aria-expanded={!!history} onClick={() => void openHistory()}>
              {history ? "← 대화로" : "지난 대화"}
            </button>
            <strong>영상 에이전트</strong>
            <button disabled={!questions.length || !!pending} onClick={() => { reset(); setHistory(null); }}>
              새 대화
            </button>
          </header>
          {history && (
            <div className="vsx-history" aria-label="지난 대화">
              {!history.length && <p className="vsx-history-empty">지난 대화가 없습니다.</p>}
              {history.map((c) => (
                <div key={c.id} className={`vsx-history-item${c.id === conversationId ? " current" : ""}`}>
                  <button className="vsx-history-open" onClick={() => void openConversation(c.id)}>
                    <strong>{c.title}</strong>
                    <small>질문 {c.questions}개 · {stamp(c.updatedAt)}</small>
                  </button>
                  <button className="vsx-history-delete" aria-label={`${c.title} 대화 삭제`} onClick={() => void removeConversation(c)}>
                    삭제
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="vsx-messages" hidden={!!history}>
            {!questions.length && (
              <div className="vsx-hello">
                <h2>무엇을 찾을까요?</h2>
                <p>분석된 현장 영상에서 보호구·시간·장면 조건으로 구간을 찾습니다.</p>
                {EXAMPLES.map((example) => (
                  <button key={example} onClick={() => { setMessage(example); input.current?.focus(); }}>
                    {example}
                  </button>
                ))}
              </div>
            )}
            {questions.map((q) => (
              <div key={q.id} className="vsx-turn">
                <p className="vsx-user">
                  <span aria-hidden="true">◯</span>
                  {q.message}
                </p>
                <div className={`vsx-bot${selected === q.id && !manual ? " selected" : ""}`} onClick={() => { if (q.status === "COMPLETED") { setSelected(q.id); setManual(null); } }}>
                  {(q.status === "QUEUED" || q.status === "RUNNING") && (
                    <div className="vsx-step live">
                      <span className="dot" />
                      {q.status === "QUEUED" ? "질문 대기 중" : "질문 해석 중"} · {Math.max(0, Math.round((now - new Date(q.createdAt).getTime()) / 1000))}초
                    </div>
                  )}
                  {(q.status === "FAILED" || q.status === "CANCELED") && <p className="vsx-error">{q.error || "질문을 처리하지 못했습니다."}</p>}
                  {q.status === "COMPLETED" && q.filter && (
                    <>
                      <details className="vsx-steps">
                        <summary>
                          <span className="dot ok" /> 중간 단계 <small>{q.elapsedMs != null ? `${(q.elapsedMs / 1000).toFixed(1)}초` : ""}</small>
                        </summary>
                        {q.trace && (
                          <>
                            <details>
                              <summary><span className="dot ok" /> 함수 시작: 질문 해석 ({q.trace.model})</summary>
                              <pre>{[...q.trace.context, "", "질문: " + q.trace.message].join("\n")}</pre>
                            </details>
                            <details>
                              <summary><span className="dot ok" /> 함수 완료: 조건 JSON</summary>
                              <pre>{JSON.stringify(q.trace.output, null, 2)}</pre>
                              <small>
                                입력 {q.trace.promptTokens ?? "?"} 토큰 · 출력 {q.trace.outputTokens ?? "?"} 토큰 · 기간은 코드로 계산
                              </small>
                            </details>
                          </>
                        )}
                        <details open>
                          <summary><span className="dot ok" /> 조건 검색: PostgreSQL</summary>
                          <div className="vsx-chips">{conditions(q.filter).map((c) => <span key={c}>{c}</span>)}</div>
                        </details>
                      </details>
                      <p className="vsx-reply">{q.reply}</p>
                      <p className="vsx-found">{q.results?.length ? `${q.results.length}개 구간을 찾았습니다.` : "조건에 맞는 구간이 없습니다."}</p>
                      {!!q.results?.length && (
                        <div className="vsx-table-wrap">
                          <table className="vsx-table">
                            <thead>
                              <tr><th>#</th><th>카메라</th><th>시작</th><th>끝</th><th>판정</th></tr>
                            </thead>
                            <tbody>
                              {q.results.slice(0, 10).map((s, i) => (
                                <tr key={s.batchId} onClick={() => s.playbackPath && setPlaying({ segment: s, key: Date.now() })}>
                                  <td>{i + 1}</td>
                                  <td>{s.cameraId}</td>
                                  <td>{clock(s.startedAt)}</td>
                                  <td>{clock(s.endedAt)}</td>
                                  <td>
                                    {(s.matched.length ? s.persons.filter((p) => s.matched.includes(p.trackId)) : []).map((p) => (
                                      <span key={p.trackId}>{p.trackId} {helmetNames[p.helmet]}·{vestNames[p.vest]}</span>
                                    ))}
                                    {!s.matched.length && <span>{s.summary.slice(0, 30)}…</span>}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {q.results.length > 10 && <small>나머지 {q.results.length - 10}개는 가운데 결과에서 확인하세요.</small>}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>
          {error && (
            <p className="vsx-error" role="alert">
              {error}
            </p>
          )}
          <div className="vsx-actions">
            {pending ? (
              <button onClick={stop}>■ 생성 중지</button>
            ) : (
              last && <button onClick={() => ask(last.message)}>↻ 다시 생성</button>
            )}
          </div>
          <form
            className="vsx-input"
            onSubmit={(e) => {
              e.preventDefault();
              void ask(message);
            }}
          >
            <input ref={input} value={message} maxLength={500} placeholder="현장 영상에 대해 물어보세요" onChange={(e) => setMessage(e.target.value)} aria-label="질문" />
            <button disabled={!message.trim() || !!pending || sending}>보내기</button>
          </form>
        </section>
      </div>
      </main>
    </div>
  );
}
