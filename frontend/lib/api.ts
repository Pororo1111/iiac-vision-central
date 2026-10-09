export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(response.status, data.error || "REQUEST_FAILED");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export async function authPost<T>(
  path: string,
  body?: URLSearchParams,
): Promise<T> {
  const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
  return api<T>(path, {
    method: "POST",
    headers: {
      [csrf.headerName]: csrf.token,
      ...(body ? { "Content-Type": "application/x-www-form-urlencoded" } : {}),
    },
    body,
  });
}
export async function csrfJson<T>(path: string, body: unknown = {}): Promise<T> {
  const csrf = await api<{ headerName: string; token: string }>("/auth/csrf");
  return api<T>(path, {
    method: "POST",
    headers: {
      [csrf.headerName]: csrf.token,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}
export type Device = {
  deviceId: string;
  name: string;
  modelName: string;
  runtimeStatus: string;
  lastSeenAt: string | null;
  registeredAt: string | null;
  online: boolean;
  monitoringAction: "PAUSE" | "RESUME" | null;
  monitoringRequestedAt: string | null;
  /** IoT가 heartbeat로 현재 감시 명령 반영을 확인했는지(확인 보고를 지원하는 IoT만). */
  monitoringApplied: boolean;
  lastError: string | null;
  startedAt: string | null;
  restartTarget: "WORKER" | "API" | "ALL" | null;
  restartRequestedAt: string | null;
  restartApplied: boolean;
  /** 중앙 모델 목록에서 찾은 실행 모델 이름. 찾지 못하면 null. */
  modelDisplayName: string | null;
};
export type DeviceLog = {
  service: string;
  requestId: string;
  content: string;
  collectedAt: string;
  receivedAt: string;
};
export type DeviceLogs = {
  request: { requestId: string; requestedAt: string; lines: number } | null;
  pending: boolean;
  logs: DeviceLog[];
};
export type DeviceCamera = {
  deviceId: string;
  cameraId: string;
  displayName: string;
  streamStatus: "STARTING" | "STREAMING" | "OFFLINE" | "ERROR";
  width: number | null;
  height: number | null;
  fps: number | null;
  lastStreamAt: string | null;
  lastError: string | null;
  updatedAt: string;
  /** 카메라 장치의 원본 해상도·이름(IoT 선택 보고). width/height는 송출 해상도다. */
  sourceWidth: number | null;
  sourceHeight: number | null;
  sourceDevice: string | null;
};
export type SizePeriod = { width: number; height: number; count: number; firstAt: string; lastAt: string };
export type CameraSources = {
  reported: { sourceWidth: number; sourceHeight: number; sourceDevice: string | null; firstSeenAt: string; lastSeenAt: string }[];
  snapshots: SizePeriod[];
  samples: SizePeriod[];
};
export type Event = {
  eventId: string;
  deviceId: string;
  cameraId: string;
  zoneId: string;
  type: string;
  occurredAt: string;
  receivedAt: string;
  objectCount: number;
};
export type EventPage = {
  items: Event[];
  total: number;
  page: number;
  size: number;
};
