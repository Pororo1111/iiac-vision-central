"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, csrfJson, type Device, type DeviceLogs } from "../../lib/api";
import { monitoringState, restartNames, restartState, runtimeNames } from "../../lib/device-status";
import CameraSourcesPanel from "./camera-sources";
import LiveCamera from "./live-camera";
import SideNav from "./side-nav";

const LOG_TTL_MS = 120_000;

function timestamp(value: string | null) {
  return value
    ? new Date(value).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "정보 없음";
}

/** 오류·경고 줄을 눈에 띄게 표시한다. */
function lineClass(line: string) {
  if (/\b(error|exception|traceback|critical|fatal)\b|오류|실패/i.test(line)) return "log-error";
  if (/\bwarn(ing)?\b|경고/i.test(line)) return "log-warn";
  return undefined;
}

export default function DeviceDetail({ deviceId }: { deviceId: string }) {
  const router = useRouter();
  const [device, setDevice] = useState<Device | null>(null);
  const [logs, setLogs] = useState<DeviceLogs | null>(null);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState("");
  const [lines, setLines] = useState(300);
  const [service, setService] = useState("");
  const [filter, setFilter] = useState("");
  const [live, setLive] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const logBox = useRef<HTMLPreElement>(null);
  const logsPendingRef = useRef(false);
  const path = `/devices/${encodeURIComponent(deviceId)}`;

  function fail(e: unknown, message: string) {
    if (e instanceof ApiError && e.status === 401) router.replace("/login");
    else if (e instanceof ApiError && e.status === 404) setMissing(true);
    else if (e instanceof ApiError && e.status === 409) setError("장치가 오프라인이라 요청을 보낼 수 없습니다.");
    else setError(message);
  }

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const [d, l] = await Promise.all([api<Device>(path), api<DeviceLogs>(`${path}/logs`)]);
        if (!active) return;
        setDevice(d);
        setLogs(l);
        setNow(Date.now());
        setError((current) => (current.startsWith("최신 정보") ? "" : current));
      } catch (e) {
        if (active) fail(e, "최신 정보를 받지 못했습니다. 마지막 수신 정보를 표시합니다.");
      } finally {
        // 로그를 기다리는 동안에는 더 자주 확인한다.
        if (active) timer = setTimeout(load, logsPendingRef.current ? 2000 : 5000);
      }
    }
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [path]);

  const logRequestedAt = logs?.request ? new Date(logs.request.requestedAt).getTime() : 0;
  const logsWaiting = !!logs?.pending && now - logRequestedAt < LOG_TTL_MS;
  const logsExpired = !!logs?.pending && !logsWaiting;
  useEffect(() => {
    logsPendingRef.current = logsWaiting;
  }, [logsWaiting]);

  const services = useMemo(() => logs?.logs.map((l) => l.service) ?? [], [logs]);
  const current = logs?.logs.find((l) => l.service === service) ?? logs?.logs[0];
  const shown = useMemo(() => {
    const all = current?.content.split("\n") ?? [];
    if (!filter.trim()) return all;
    const q = filter.trim().toLowerCase();
    return all.filter((line) => line.toLowerCase().includes(q));
  }, [current, filter]);
  useEffect(() => {
    if (logBox.current) logBox.current.scrollTop = logBox.current.scrollHeight;
  }, [current?.requestId, current?.service]);

  async function monitoring(action: "PAUSE" | "RESUME") {
    if (!device) return;
    const verb = action === "PAUSE" ? "중지" : "재개";
    if (!window.confirm(`${device.name}의 감시를 ${verb}할까요? IoT가 다음 heartbeat(최대 약 5초)에 반영합니다.`)) return;
    setBusy("monitoring");
    try {
      setDevice(await csrfJson<Device>(`${path}/monitoring`, { action }));
      setNow(Date.now());
    } catch (e) {
      fail(e, `감시 ${verb} 요청을 보내지 못했습니다.`);
    } finally {
      setBusy("");
    }
  }

  async function restart(target: "WORKER" | "API" | "ALL") {
    if (!device) return;
    const name = restartNames[target];
    const note = target === "WORKER" ? "추론이 잠시 멈춥니다." : "재시작 동안 heartbeat·영상 연결이 잠시 끊깁니다.";
    if (!window.confirm(`${device.name}의 ${name}를 재시작할까요? ${note}\n2분 안에 IoT가 받지 않으면 명령은 만료됩니다.`)) return;
    setBusy("restart");
    try {
      setDevice(await csrfJson<Device>(`${path}/restart`, { target }));
      setNow(Date.now());
    } catch (e) {
      fail(e, "재시작 요청을 보내지 못했습니다.");
    } finally {
      setBusy("");
    }
  }

  async function requestLogs() {
    setBusy("logs");
    try {
      setLogs(await csrfJson<DeviceLogs>(`${path}/logs/request`, { lines }));
      setNow(Date.now());
      logsPendingRef.current = true;
    } catch (e) {
      fail(e, "로그 요청을 보내지 못했습니다.");
    } finally {
      setBusy("");
    }
  }

  if (missing)
    return (
      <div className="console-shell">
        <SideNav active="dashboard" />
        <div className="console-main">
          <main className="content">
            <Link href="/">← 통합 관제</Link>
            <p className="error">등록된 장치가 아닙니다: {deviceId}</p>
          </main>
        </div>
      </div>
    );

  const mState = device ? monitoringState(device, now) : null;
  const rState = device ? restartState(device, now) : null;
  const running = device?.runtimeStatus === "RUNNING";
  return (
    <div className="console-shell">
      <SideNav active="dashboard" />
      <div className="console-main">
        <header className="topbar">
          <span>
            <Link href="/">통합 관제</Link> <span className="divider">/</span> <strong>장치 상세</strong>
          </span>
        </header>
        <main className="content device-detail">
          <Link className="training-back" href="/">
            ← 뒤로가기 · 통합 관제
          </Link>
          <div className="page-heading">
            <div>
              <p className="eyebrow">DEVICE DETAIL</p>
              <h1>{device?.name ?? deviceId}</h1>
              <p className="muted mono">{deviceId}</p>
            </div>
            <button className="camera-open" disabled={!device?.online} onClick={() => setLive(true)}>
              {device?.online ? "카메라 보기" : "오프라인"}
            </button>
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {!device ? (
            <p className="muted">장치 정보를 불러오는 중…</p>
          ) : (
            <>
              <section className="device-facts" aria-label="장치 상태">
                <article>
                  <span>연결</span>
                  <strong className={device.online ? "ok-text" : "gray"}>{device.online ? "온라인" : "오프라인"}</strong>
                  <small>마지막 응답 {timestamp(device.lastSeenAt)}</small>
                </article>
                <article>
                  <span>감지 상태</span>
                  <strong className={device.runtimeStatus === "ERROR" ? "warn-text" : undefined}>
                    {runtimeNames[device.runtimeStatus] || device.runtimeStatus}
                  </strong>
                  <small>{device.online ? "heartbeat 보고 기준" : "마지막 보고 상태"}</small>
                </article>
                <article>
                  <span>실행 모델</span>
                  <strong className="fact-model" title={device.modelName || undefined}>
                    {device.modelDisplayName || device.modelName || "모델 정보 없음"}
                  </strong>
                  <small>IoT 기동 {timestamp(device.startedAt)}</small>
                </article>
                <article className={device.lastError ? "fact-alert" : undefined}>
                  <span>최근 오류</span>
                  <strong className="fact-error">{device.lastError || "보고된 오류 없음"}</strong>
                  <small>IoT가 heartbeat로 보고한 오류</small>
                </article>
              </section>

              <CameraSourcesPanel deviceId={deviceId} />

              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>감시 제어</h2>
                    <p>감지 실행을 중지하거나 재개합니다. IoT가 다음 heartbeat에 반영합니다.</p>
                  </div>
                  <span className="small-label">MONITORING</span>
                </div>
                <div className="device-actions">
                  <button
                    className={running ? "monitor-stop" : "monitor-start"}
                    disabled={busy === "monitoring" || mState?.kind === "waiting"}
                    onClick={() => void monitoring(running ? "PAUSE" : "RESUME")}
                  >
                    {running ? "감시 중지" : "감시 재개"}
                  </button>
                  {mState && <span className={mState.kind === "issue" ? "warn-text" : "muted"}>{mState.text}</span>}
                  {!mState && device.monitoringAction && (
                    <span className="muted">
                      마지막 요청: {device.monitoringAction === "PAUSE" ? "중지" : "재개"} · {timestamp(device.monitoringRequestedAt)}
                    </span>
                  )}
                </div>
              </section>

              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>서버 재시작</h2>
                    <p>IoT 장비의 서비스를 재시작합니다. 연결된 장치에만 요청할 수 있으며 2분 안에 전달되지 않으면 만료됩니다.</p>
                  </div>
                  <span className="small-label">RESTART</span>
                </div>
                <div className="device-actions">
                  {(["WORKER", "API", "ALL"] as const).map((target) => (
                    <button
                      key={target}
                      className="restart-button"
                      disabled={!device.online || busy === "restart" || rState?.kind === "waiting"}
                      onClick={() => void restart(target)}
                    >
                      {restartNames[target]} 재시작
                    </button>
                  ))}
                  {rState && (
                    <span className={rState.kind === "expired" ? "warn-text" : "muted"}>
                      {rState.text} · 요청 {timestamp(device.restartRequestedAt)}
                    </span>
                  )}
                </div>
              </section>

              <section className="panel" id="logs">
                <div className="panel-heading">
                  <div>
                    <h2>서버 로그</h2>
                    <p>IoT가 다음 heartbeat에서 요청을 받아 최근 로그를 올립니다. 장치별 마지막 응답만 보관합니다.</p>
                  </div>
                  <span className="small-label">LOGS</span>
                </div>
                <div className="device-actions">
                  <label className="inline-field">
                    줄 수
                    <select value={lines} onChange={(e) => setLines(Number(e.target.value))}>
                      {[100, 300, 1000, 2000].map((n) => (
                        <option key={n} value={n}>
                          최근 {n}줄
                        </option>
                      ))}
                    </select>
                  </label>
                  <button className="collection-primary" disabled={!device.online || busy === "logs" || logsWaiting} onClick={() => void requestLogs()}>
                    {logsWaiting ? "로그 받는 중…" : "로그 가져오기"}
                  </button>
                  {logsExpired && <span className="warn-text">IoT가 2분 안에 로그를 보내지 않았습니다. IoT가 로그 요청을 지원하는지 확인하세요.</span>}
                  {!logsWaiting && !logsExpired && current && <span className="muted">수집 {timestamp(current.collectedAt)} · 수신 {timestamp(current.receivedAt)}</span>}
                  {!device.online && <span className="muted">오프라인 장치에는 요청할 수 없습니다.</span>}
                </div>
                {logs && logs.logs.length > 0 ? (
                  <div className="log-viewer">
                    <div className="log-toolbar">
                      <div className="log-tabs" role="tablist">
                        {services.map((name) => (
                          <button
                            key={name}
                            role="tab"
                            aria-selected={current?.service === name}
                            className={current?.service === name ? "active" : undefined}
                            onClick={() => setService(name)}
                          >
                            {name}
                          </button>
                        ))}
                      </div>
                      <input placeholder="검색(예: error)" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="로그 검색" />
                    </div>
                    <pre ref={logBox} className="log-lines">
                      {shown.map((line, i) => (
                        <span key={i} className={lineClass(line)}>
                          {line}
                          {"\n"}
                        </span>
                      ))}
                      {!shown.length && <span className="muted">일치하는 줄이 없습니다.</span>}
                    </pre>
                  </div>
                ) : (
                  <div className="empty">{logsWaiting ? "IoT 응답을 기다리는 중…" : "받은 로그가 없습니다. 로그 가져오기를 누르세요."}</div>
                )}
              </section>
            </>
          )}
        </main>
      </div>
      {live && device && <LiveCamera device={device} onClose={() => setLive(false)} />}
    </div>
  );
}
