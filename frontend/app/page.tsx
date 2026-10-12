"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api,
  ApiError,
  authPost,
  csrfJson,
  type Device,
  type EventPage,
} from "../lib/api";
import LiveCamera from "./components/live-camera";
import SideNav from "./components/side-nav";
import Link from "next/link";
import { monitoringState, runtimeNames } from "../lib/device-status";
function timestamp(value: string | null) {
  return value
    ? new Date(value).toLocaleString("ko-KR", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "수신 전";
}
const eventNames: Record<string, string> = {
  INTRUSION_START: "침입 시작",
  INTRUSION_END: "침입 종료",
};
const eventBadges: Record<string, string> = {
  INTRUSION_START: "warning",
  INTRUSION_END: "success",
};
export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState("");
  const [devices, setDevices] = useState<Device[]>([]);
  const [events, setEvents] = useState<EventPage>({
    items: [],
    total: 0,
    page: 0,
    size: 20,
  });
  const [deviceId, setDeviceId] = useState("");
  const [type, setType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [liveDevice, setLiveDevice] = useState<Device | null>(null);
  const [monitoringBusy, setMonitoringBusy] = useState("");
  async function requestMonitoring(d: Device, action: "PAUSE" | "RESUME") {
    const verb = action === "PAUSE" ? "중지" : "재개";
    if (!window.confirm(`${d.name}의 감시를 ${verb}할까요? IoT가 다음 heartbeat(최대 약 5초)에 반영합니다.`)) return;
    setMonitoringBusy(d.deviceId);
    try {
      const updated = await csrfJson<Device>(`/devices/${encodeURIComponent(d.deviceId)}/monitoring`, { action });
      setDevices((current) => current.map((item) => (item.deviceId === updated.deviceId ? updated : item)));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(`감시 ${verb} 요청을 보내지 못했습니다.`);
    } finally {
      setMonitoringBusy("");
    }
  }
  useEffect(() => {
    let active = true;
    api<{ username: string }>("/auth/me")
      .then((data) => {
        if (active) setUser(data.username);
      })
      .catch((e) => {
        if (active) {
          if (e instanceof ApiError && e.status === 401)
            router.replace("/login");
          else setError("서버에 연결할 수 없습니다. 새로고침해 주세요.");
        }
      });
    return () => {
      active = false;
    };
  }, [router, refresh]);
  useEffect(() => {
    if (!user) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function load() {
      try {
        if (from && to && new Date(from) > new Date(to)) {
          setError("조회 시작 시각은 종료 시각보다 빨라야 합니다.");
          return;
        }
        // 쓰러짐 이벤트는 모델 정확도가 낮아 통합 관제에 표시하지 않는다(영상 검색 대화에서만 조회).
        const params = new URLSearchParams({ page: String(page), size: "20", intrusionOnly: "true" });
        if (deviceId) params.set("deviceId", deviceId);
        if (type) params.set("type", type);
        if (from) params.set("from", new Date(from).toISOString());
        if (to) params.set("to", new Date(to).toISOString());
        const [d, e] = await Promise.all([
          api<Device[]>("/devices", { signal: controller.signal }),
          api<EventPage>(`/events?${params}`, { signal: controller.signal }),
        ]);
        if (active) {
          setDevices(d);
          setEvents(e);
          setLastUpdate(new Date().toISOString());
          setError("");
        }
      } catch (e) {
        if (active) {
          if (e instanceof ApiError && e.status === 401)
            router.replace("/login");
          else
            setError(
              "최신 정보를 받지 못했습니다. 마지막 수신 정보를 표시합니다.",
            );
        }
      } finally {
        if (active) {
          setLoading(false);
          timer = setTimeout(load, 5000);
        }
      }
    }
    setLoading(true);
    void load();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [user, deviceId, type, from, to, page, refresh, router]);
  async function logout() {
    setLogoutBusy(true);
    try {
      await authPost("/auth/logout");
      router.replace("/login");
    } catch {
      setError("로그아웃에 실패했습니다. 다시 시도하세요.");
      setLogoutBusy(false);
    }
  }
  const online = devices.filter((d) => d.online).length;
  if (!user)
    return (
      <main className="auth-wait">
        <div className="brand">
          FIELD<span>CONTROL</span>
        </div>
        <p role="status">{error || "로그인 상태를 확인하고 있습니다…"}</p>
        {error && (
          <button onClick={() => setRefresh((v) => v + 1)}>다시 시도</button>
        )}
      </main>
    );
  return (
    <div className="console-shell">
      <SideNav active="dashboard" />
      <div className="console-main">
        <header className="topbar">
          <span>
            워크스페이스 <span className="divider">/</span>{" "}
            <strong>통합 관제</strong>
          </span>
          <div className="account">
            <span className="avatar">A</span>
            <span>{user}</span>
            <button
              className="text-button"
              onClick={logout}
              disabled={logoutBusy}
            >
              로그아웃
            </button>
          </div>
        </header>
        <main className="content" id="overview">
          <div className="page-heading">
            <div>
              <p className="eyebrow">OPERATIONS OVERVIEW</p>
              <h1>현장 모니터링</h1>
              <p className="muted">
                연결된 장치와 현장에서 발생한 이벤트를 확인하세요.
              </p>
            </div>
            <button
              onClick={() => setRefresh((v) => v + 1)}
              disabled={loading}
              className="refresh-button"
            >
              <span aria-hidden="true">↻</span> 새로고침
            </button>
          </div>
          <div className="update-line">
            <span className={`live-dot ${error ? "bad" : ""}`} />
            <span>{error ? "연결 확인 필요" : "5초마다 자동 갱신"}</span>
            <span className="last-update">
              마지막 갱신 {timestamp(lastUpdate)}
            </span>
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <section className="metrics" aria-label="장치 요약">
            <article>
              <p>
                전체 장치 <span>01</span>
              </p>
              <strong>
                {devices.length}
                <small>대</small>
              </strong>
              <span className="metric-caption">사전 등록된 현장 장치</span>
            </article>
            <article>
              <p>
                온라인 <span className="green">●</span>
              </p>
              <strong>
                {online}
                <small>대</small>
              </strong>
              <span className="metric-caption">최근 30초 이내 응답</span>
            </article>
            <article>
              <p>
                오프라인 <span className="gray">●</span>
              </p>
              <strong>
                {devices.length - online}
                <small>대</small>
              </strong>
              <span className="metric-caption">연결 확인이 필요한 장치</span>
            </article>
            <article>
              <p>
                조회 이벤트 <span>↗</span>
              </p>
              <strong>
                {events.total.toLocaleString()}
                <small>건</small>
              </strong>
              <span className="metric-caption">현재 검색 조건 기준</span>
            </article>
          </section>
          <section className="panel" id="devices">
            <div className="panel-heading">
              <div>
                <h2>
                  현장 장치 <span className="count">{devices.length}</span>
                </h2>
                <p>통신 연결과 감지 실행 상태를 따로 확인합니다.</p>
              </div>
              <span className="small-label">DEVICE STATUS</span>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>장치</th>
                    <th>연결 상태</th>
                    <th>감지 상태</th>
                    <th>실행 모델</th>
                    <th>마지막 응답</th>
                    <th>감시 제어</th>
                    <th>실시간 영상</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => (
                    <tr
                      key={d.deviceId}
                      className="device-row"
                      onClick={(e) => {
                        if (!(e.target as HTMLElement).closest("button,a")) router.push(`/devices/${encodeURIComponent(d.deviceId)}`);
                      }}
                    >
                      <td>
                        <Link className="device-link" href={`/devices/${encodeURIComponent(d.deviceId)}`}>
                          <strong>{d.name}</strong>
                        </Link>
                        <span className="subline mono">{d.deviceId}</span>
                        {d.lastError && <span className="subline warn-text">{d.lastError}</span>}
                      </td>
                      <td>
                        <span
                          className={`badge ${d.online ? "success" : "neutral"}`}
                        >
                          {d.online ? "온라인" : "오프라인"}
                        </span>
                      </td>
                      <td>
                        {runtimeNames[d.runtimeStatus] || d.runtimeStatus}
                        {!d.online && d.runtimeStatus !== "UNKNOWN" && (
                          <span className="subline">마지막 보고 상태</span>
                        )}
                      </td>
                      <td className="mono model-cell">
                        {d.modelName || "모델 정보 없음"}
                      </td>
                      <td className="tabular">{timestamp(d.lastSeenAt)}</td>
                      <td>
                        {(() => {
                          const state = monitoringState(d);
                          const running = d.runtimeStatus === "RUNNING";
                          return (
                            <>
                              <button
                                className={running ? "monitor-stop" : "monitor-start"}
                                disabled={monitoringBusy === d.deviceId || state?.kind === "waiting"}
                                onClick={() => void requestMonitoring(d, running ? "PAUSE" : "RESUME")}
                              >
                                {running ? "감시 중지" : "감시 재개"}
                              </button>
                              {state && (
                                <span className={`subline ${state.kind === "issue" ? "warn-text" : ""}`}>
                                  {state.text}
                                  {state.kind === "issue" && d.online && (
                                    <>
                                      {" "}
                                      <Link href={`/devices/${encodeURIComponent(d.deviceId)}#logs`}>로그 보기</Link>
                                    </>
                                  )}
                                </span>
                              )}
                            </>
                          );
                        })()}
                      </td>
                      <td>
                        <button
                          className="camera-open"
                          disabled={!d.online}
                          onClick={() => setLiveDevice(d)}
                        >
                          {d.online ? "카메라 보기" : "오프라인"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!devices.length && (
                <div className="empty">
                  {loading
                    ? "장치 목록을 불러오는 중…"
                    : "등록된 장치가 없습니다."}
                </div>
              )}
            </div>
          </section>
          <section className="panel" id="events">
            <div className="panel-heading">
              <div>
                <h2>현장 이벤트</h2>
                <p>침입 시작·종료와 쓰러짐 감지·해제 기록입니다.</p>
              </div>
              <span className="small-label">EVENT LOG</span>
            </div>
            <div className="filters">
              <label>
                장치
                <select
                  value={deviceId}
                  onChange={(e) => {
                    setDeviceId(e.target.value);
                    setPage(0);
                  }}
                >
                  <option value="">전체 장치</option>
                  {devices.map((d) => (
                    <option value={d.deviceId} key={d.deviceId}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                이벤트
                <select
                  value={type}
                  onChange={(e) => {
                    setType(e.target.value);
                    setPage(0);
                  }}
                >
                  <option value="">전체 이벤트</option>
                  <option value="INTRUSION_START">침입 시작</option>
                  <option value="INTRUSION_END">침입 종료</option>
                </select>
              </label>
              <label>
                시작 시각
                <input
                  type="datetime-local"
                  value={from}
                  onChange={(e) => {
                    setFrom(e.target.value);
                    setPage(0);
                  }}
                />
              </label>
              <label>
                종료 시각
                <input
                  type="datetime-local"
                  value={to}
                  onChange={(e) => {
                    setTo(e.target.value);
                    setPage(0);
                  }}
                />
              </label>
              <button
                className="reset-filter"
                onClick={() => {
                  setDeviceId("");
                  setType("");
                  setFrom("");
                  setTo("");
                  setPage(0);
                }}
              >
                초기화
              </button>
            </div>
            <div className="table-scroll" aria-busy={loading}>
              <table>
                <thead>
                  <tr>
                    <th>발생 시각</th>
                    <th>이벤트</th>
                    <th>장치</th>
                    <th>카메라 / 영역</th>
                    <th>객체 수</th>
                    <th>수신 시각</th>
                  </tr>
                </thead>
                <tbody>
                  {events.items.map((e) => (
                    <tr key={e.eventId}>
                      <td className="tabular">{timestamp(e.occurredAt)}</td>
                      <td>
                        <span className={`badge ${eventBadges[e.type] ?? "neutral"}`}>
                          {eventNames[e.type] ?? e.type}
                        </span>
                      </td>
                      <td className="mono">{e.deviceId}</td>
                      <td>
                        {e.cameraId}
                        <span className="subline">{e.zoneId || "카메라 전체"}</span>
                      </td>
                      <td>{e.objectCount}개</td>
                      <td className="tabular">{timestamp(e.receivedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!events.items.length && (
                <div className="empty">
                  <span className="empty-icon" aria-hidden="true">
                    ◎
                  </span>
                  <strong>
                    {loading
                      ? "이벤트를 불러오는 중…"
                      : "조회된 이벤트가 없습니다."}
                  </strong>
                  <p>새로운 이벤트가 도착하면 여기에 표시됩니다.</p>
                </div>
              )}
            </div>
            <div className="pagination">
              <span>
                총 {events.total.toLocaleString()}건 · {page + 1} /{" "}
                {Math.max(1, Math.ceil(events.total / 20))} 페이지
              </span>
              <div>
                <button
                  disabled={page === 0 || loading}
                  onClick={() => setPage((v) => v - 1)}
                >
                  ← 이전
                </button>
                <button
                  disabled={(page + 1) * 20 >= events.total || loading}
                  onClick={() => setPage((v) => v + 1)}
                >
                  다음 →
                </button>
              </div>
            </div>
          </section>
          <footer className="page-footer">
            FIELD CONTROL{" "}
            <span>모든 시각은 접속 기기의 현지 시간으로 표시됩니다.</span>
          </footer>
        </main>
      </div>
      {liveDevice && <LiveCamera device={liveDevice} onClose={() => setLiveDevice(null)} />}
    </div>
  );
}
