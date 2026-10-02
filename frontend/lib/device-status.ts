import type { Device } from "./api";

export const runtimeNames: Record<string, string> = {
  RUNNING: "감지 중",
  STOPPED: "중지",
  STALE: "결과 지연",
  ERROR: "오류",
  UNKNOWN: "대기",
};

/** IoT가 명령을 받는 데 걸리는 시간(heartbeat 5초)을 넉넉히 본 대기 한도. 이후에는 원인을 표시한다. */
const WAIT_MS = 20_000;

export type MonitoringState = { kind: "waiting" | "issue"; text: string } | null;

/** 중앙이 요청한 감시 명령이 IoT 보고 상태에 반영됐는지. 반영됐지만 감지가 안 되면 대기 대신 원인을 보여준다. */
export function monitoringState(d: Device, now = Date.now()): MonitoringState {
  if (!d.monitoringAction || !d.monitoringRequestedAt) return null;
  const pause = d.monitoringAction === "PAUSE";
  const mismatch = pause ? d.runtimeStatus === "RUNNING" : d.runtimeStatus !== "RUNNING";
  if (!mismatch) return null;
  const verb = pause ? "중지" : "재개";
  const elapsed = now - new Date(d.monitoringRequestedAt).getTime();
  const status = runtimeNames[d.runtimeStatus] || d.runtimeStatus;
  const reason = d.lastError ? ` · ${d.lastError}` : "";
  if (!d.online) return { kind: "issue", text: `오프라인 · 연결되면 ${verb} 반영` };
  if (d.monitoringApplied) {
    if (!pause && d.runtimeStatus === "UNKNOWN" && !d.lastError && elapsed < WAIT_MS)
      return { kind: "waiting", text: "재개 반영됨 · 감지 시작 대기…" };
    return { kind: "issue", text: `${verb} 반영됨 · 감지 ${status}${reason}` };
  }
  if (d.runtimeStatus === "ERROR" || d.lastError)
    return { kind: "issue", text: `감지 ${status}${reason || " · 로그를 확인하세요"}` };
  if (elapsed < WAIT_MS) return { kind: "waiting", text: `${verb} 요청 반영 대기…` };
  return { kind: "issue", text: `${verb} 반영 확인 안 됨 · 감지 ${status} · 로그를 확인하세요` };
}

const RESTART_TTL_MS = 120_000;
export const restartNames = { WORKER: "추론 워커", API: "API 서버", ALL: "전체 서비스" } as const;

/** 재시작 명령 상태. 중앙은 2분 안의 heartbeat에만 명령을 전달한다. */
export function restartState(d: Device, now = Date.now()) {
  if (!d.restartTarget || !d.restartRequestedAt) return null;
  const name = restartNames[d.restartTarget];
  const elapsed = now - new Date(d.restartRequestedAt).getTime();
  if (d.restartApplied) return { kind: "done" as const, text: `${name} 재시작 명령 수신 확인` };
  if (elapsed < RESTART_TTL_MS) return { kind: "waiting" as const, text: `${name} 재시작 명령 전달 대기…` };
  return { kind: "expired" as const, text: `${name} 재시작 명령이 확인되지 않고 만료됨` };
}
