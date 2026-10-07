"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { ApiError, api, type Device, type DeviceCamera } from "../../lib/api";

type ViewSession = { whepUrl: string; token: string; expiresAt: string };
type Csrf = { headerName: string; token: string };
type Phase = "waiting" | "connecting" | "playing" | "paused" | "missing";

/** 송출 임대 연장 주기. 서버 임대 길이(기본 60초)보다 충분히 짧게 둔다. */
const EXTEND_MS = 30_000;
/** 탭이 이 시간 넘게 숨겨져 있으면 연장을 멈춰 임대가 저절로 만료되게 한다. */
const HIDDEN_LIMIT_MS = 60_000;
const POLL_MS = 1_000;
/** 같은 카메라로 WebRTC 연결을 다시 시도하는 최소 간격 */
const RETRY_MS = 1_000;

function waitForIce(pc: RTCPeerConnection) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise<void>((resolve) => {
    const done = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", done);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", done);
  });
}

function isSimulation(camera: DeviceCamera | undefined) {
  return !!camera && (camera.cameraId.startsWith("test-video") || camera.displayName.includes("시뮬레이션"));
}

function pickCamera(items: DeviceCamera[]) {
  return items.find((item) => item.streamStatus === "STREAMING")
    || [...items].sort((a, b) => (b.lastStreamAt || "").localeCompare(a.lastStreamAt || ""))[0];
}

export default function LiveCamera({ device, onClose }: { device: Device; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const peer = useRef<RTCPeerConnection | null>(null);
  const resource = useRef<string | null>(null);
  const token = useRef<string | null>(null);
  const playbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const csrf = useRef<Csrf | null>(null);
  // 창(시청자)마다 다른 ID. 같은 관리자가 두 창으로 봐도 한 창을 닫을 때 다른 창의 시청을 끊지 않는다.
  const viewerId = useRef<string>("");
  const [cameras, setCameras] = useState<DeviceCamera[]>([]);
  const [cameraId, setCameraId] = useState("");
  const [current, setCurrent] = useState<Device>(device);
  const [phase, setPhase] = useState<Phase>("waiting");
  const [waited, setWaited] = useState(0);
  const [error, setError] = useState("");
  const [frame, setFrame] = useState<{ width: number; height: number } | null>(null);

  const deviceId = device.deviceId;
  const cameraPath = useCallback(
    (camera: string) => `/devices/${encodeURIComponent(deviceId)}/cameras/${encodeURIComponent(camera)}`,
    [deviceId],
  );

  const csrfHeaders = useCallback(async (refresh = false): Promise<Record<string, string>> => {
    if (!csrf.current || refresh) csrf.current = await api<Csrf>("/auth/csrf");
    return { [csrf.current.headerName]: csrf.current.token };
  }, []);

  const disconnect = useCallback(() => {
    if (playbackTimer.current) clearTimeout(playbackTimer.current);
    playbackTimer.current = null;
    peer.current?.close();
    peer.current = null;
    if (video.current) video.current.srcObject = null;
    if (resource.current && token.current) {
      void fetch(resource.current, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token.current}` },
      }).catch(() => undefined);
    }
    resource.current = null;
    token.current = null;
  }, []);

  /** 송출이 시작된 카메라에 WebRTC로 붙는다. 실패하면 false를 돌려주고 대기 상태로 돌아간다. */
  const connect = useCallback(async (selected: string) => {
    disconnect();
    try {
      // 송출이 아직 준비되지 않았으면 409로 끝나며 대기 상태를 그대로 둔다.
      const session = await api<ViewSession>(`${cameraPath(selected)}/view-session`, {
        method: "POST",
        headers: { ...(await csrfHeaders()), "Content-Type": "application/json" },
        body: "{}",
      });
      setFrame(null);
      setPhase("connecting");
      const pc = new RTCPeerConnection();
      peer.current = pc;
      token.current = session.token;
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.ontrack = (event) => {
        if (video.current) video.current.srcObject = event.streams[0];
      };
      pc.onconnectionstatechange = () => {
        if (peer.current !== pc) return;
        if (pc.connectionState === "failed" || pc.connectionState === "disconnected" || pc.connectionState === "closed") {
          // IoT가 송출을 멈추면 연결이 끊긴다. 대기로 돌아가 송출이 다시 시작되면 자동으로 붙는다.
          disconnect();
          setPhase("waiting");
        }
      };
      await pc.setLocalDescription(await pc.createOffer());
      await waitForIce(pc);
      const response = await fetch(session.whepUrl, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.token}`, "Content-Type": "application/sdp" },
        body: pc.localDescription?.sdp,
      });
      if (!response.ok) throw new Error(`WHEP_${response.status}`);
      const location = response.headers.get("Location");
      if (location) resource.current = new URL(location, session.whepUrl).toString();
      await pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
      playbackTimer.current = setTimeout(() => {
        if (!video.current || video.current.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
          disconnect();
          setPhase("waiting");
        }
      }, 12000);
      return true;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 403) csrf.current = null;
      disconnect();
      setPhase((value) => (value === "connecting" ? "waiting" : value));
      return false;
    }
  }, [cameraPath, csrfHeaders, disconnect]);

  // 처음 열 때 카메라 목록을 받아 기본 카메라를 고른다.
  useEffect(() => {
    viewerId.current = crypto.randomUUID();
    let active = true;
    api<DeviceCamera[]>(`/devices/${encodeURIComponent(deviceId)}/cameras`)
      .then((items) => {
        if (!active) return;
        setCameras(items);
        const first = pickCamera(items);
        if (first) setCameraId(first.cameraId);
        else { setPhase("missing"); setError("등록된 카메라가 없습니다."); }
      })
      .catch(() => { if (active) setError("카메라 정보를 불러오지 못했습니다."); });
    return () => { active = false; };
  }, [deviceId]);

  // 선택한 카메라의 송출 임대를 잡고, 송출이 시작되면 연결하며, 닫을 때 임대를 해제한다.
  useEffect(() => {
    if (!cameraId) return;
    let active = true;
    let busy = false;
    let lastAttempt = 0;
    let started = Date.now();
    let hiddenSince: number | null = document.hidden ? Date.now() : null;
    const leasePath = `/api${cameraPath(cameraId)}/stream-lease`;

    const acquire = async () => {
      try {
        const body = JSON.stringify({ viewerId: viewerId.current });
        const post = async (refresh: boolean) => api(`${cameraPath(cameraId)}/stream-lease`, {
          method: "POST",
          headers: { ...(await csrfHeaders(refresh)), "Content-Type": "application/json" },
          body,
        });
        await post(false).catch((reason) => {
          if (reason instanceof ApiError && reason.status === 403) return post(true);
          throw reason;
        });
        if (active) setError("");
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof ApiError && reason.status === 404
          ? "등록되지 않은 카메라입니다."
          : "송출 요청을 보내지 못했습니다. 잠시 후 다시 시도합니다.");
      }
    };
    const release = () => {
      const header = csrf.current ? { [csrf.current.headerName]: csrf.current.token } : undefined;
      void fetch(`${leasePath}?viewerId=${encodeURIComponent(viewerId.current)}`, {
        method: "DELETE",
        credentials: "same-origin",
        keepalive: true,
        headers: header,
      }).catch(() => undefined);
    };
    const tick = async () => {
      if (!active || busy) return;
      busy = true;
      try {
        const [items, latest] = await Promise.all([
          api<DeviceCamera[]>(`/devices/${encodeURIComponent(deviceId)}/cameras`),
          api<Device>(`/devices/${encodeURIComponent(deviceId)}`),
        ]);
        if (!active) return;
        setCameras(items);
        setCurrent(latest);
        if (peer.current) return;
        // 마지막 감시 명령이 PAUSE여도 IoT 화면에서 재개했을 수 있으므로 실제 감지 상태까지 본다.
        const paused = latest.monitoringAction === "PAUSE" && latest.runtimeStatus !== "RUNNING";
        setWaited(Math.floor((Date.now() - started) / 1000));
        // 카메라 상태 보고는 publish보다 늦을 수 있어, 보고 전에도 주기적으로 연결을 시도한다.
        // 서버가 MediaMTX 경로 준비를 확인하므로 송출 중이면 감시 상태와 관계없이 붙는다(IoT 수동 송출 포함).
        if (latest.online && Date.now() - lastAttempt >= RETRY_MS) {
          lastAttempt = Date.now();
          if (await connect(cameraId)) return;
        }
        if (active && !peer.current) setPhase(paused ? "paused" : "waiting");
      } catch {
        // 일시적인 조회 실패는 다음 주기에 다시 확인한다.
      } finally {
        busy = false;
      }
    };

    setPhase("waiting");
    setWaited(0);
    setError("");
    void acquire().then(tick);
    const poll = setInterval(() => void tick(), POLL_MS);
    const extend = setInterval(() => {
      if (hiddenSince !== null && Date.now() - hiddenSince > HIDDEN_LIMIT_MS) return;
      void acquire();
    }, EXTEND_MS);
    const onVisibility = () => {
      if (document.hidden) {
        hiddenSince = Date.now();
        return;
      }
      const stopped = hiddenSince !== null && Date.now() - hiddenSince > HIDDEN_LIMIT_MS;
      hiddenSince = null;
      if (stopped) {
        // 연장을 멈춘 사이 임대가 만료됐을 수 있으므로 다시 잡고 대기 시간을 새로 센다.
        started = Date.now();
        void acquire().then(tick);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", release);
    return () => {
      active = false;
      clearInterval(poll);
      clearInterval(extend);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", release);
      disconnect();
      release();
    };
  }, [cameraId, cameraPath, connect, csrfHeaders, deviceId, disconnect]);

  const selectedCamera = cameras.find((camera) => camera.cameraId === cameraId);
  const size = frame
    || (selectedCamera?.width && selectedCamera?.height ? { width: selectedCamera.width, height: selectedCamera.height } : null);
  const aspect = size ? size.width / size.height : 16 / 9;
  const updateFrame = () => {
    const element = video.current;
    if (element?.videoWidth && element.videoHeight) {
      setFrame((value) => value?.width === element.videoWidth && value.height === element.videoHeight
        ? value
        : { width: element.videoWidth, height: element.videoHeight });
    }
  };

  // 상태 표시: 송출 중 / 연결 중 / 송출 안 함 / 오류 / 감시 중지됨
  let badge = "연결 중";
  let tone = "wait";
  let message = "";
  if (phase === "playing") {
    badge = "송출 중"; tone = "ok"; message = "실시간 영상";
  } else if (phase === "paused") {
    badge = "감시 중지됨"; tone = "off"; message = "장치 감시가 중지되어 송출하지 않습니다. 장치 상세에서 감시를 재개해 주세요.";
  } else if (phase === "missing") {
    badge = "오류"; tone = "bad"; message = error;
  } else if (phase === "connecting") {
    message = "영상 연결 중…";
  } else if (!current.online) {
    badge = "송출 안 함"; tone = "off"; message = "장치가 오프라인입니다. 다시 연결되면 송출을 요청합니다.";
  } else if (waited < 10) {
    message = "현장 장치 연결 중(최대 10초)…";
  } else if (waited < 30) {
    message = `현장 장치의 송출 시작을 기다리고 있습니다… ${waited}초`;
  } else if (selectedCamera?.streamStatus === "ERROR") {
    badge = "오류"; tone = "bad";
    message = `송출 오류: ${selectedCamera.lastError || "장치가 사유를 보고하지 않았습니다."}`;
  } else {
    badge = selectedCamera?.streamStatus === "OFFLINE" ? "송출 안 함" : "연결 중";
    tone = selectedCamera?.streamStatus === "OFFLINE" ? "off" : "wait";
    message = selectedCamera?.lastError
      ? `송출이 시작되지 않았습니다: ${selectedCamera.lastError}`
      : `${waited}초 동안 송출이 시작되지 않았습니다. 장치가 송출 요청을 지원하는지 확인해 주세요.`;
  }
  if (error && phase !== "missing") message = error;

  return (
    <div className="camera-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="camera-dialog" style={{ "--camera-aspect": aspect } as CSSProperties} role="dialog" aria-modal="true" aria-labelledby="camera-title">
        <header className="camera-heading">
          <div>
            <p className="eyebrow">LIVE CAMERA</p>
            <h2 id="camera-title">{device.name}</h2>
            <span className="mono">{device.deviceId}</span>
          </div>
          <button onClick={onClose} aria-label="실시간 영상 닫기">닫기 ×</button>
        </header>
        <div className="camera-select">
          {cameras.length > 1 ? (
            <label>카메라
              <select value={cameraId} onChange={(event) => setCameraId(event.target.value)}>
                {cameras.map((camera) => <option key={camera.cameraId} value={camera.cameraId}>{camera.displayName}</option>)}
              </select>
            </label>
          ) : selectedCamera && <span>{selectedCamera.displayName}</span>}
          {isSimulation(selectedCamera) && <span className="camera-sim">시뮬레이션</span>}
          <span className={`camera-badge ${tone}`}>{badge}</span>
        </div>
        <div className="camera-stage">
          <video
            ref={video}
            autoPlay
            muted
            playsInline
            controls={phase === "playing"}
            onLoadedMetadata={updateFrame}
            onResize={updateFrame}
            onPlaying={() => {
              updateFrame();
              if (playbackTimer.current) clearTimeout(playbackTimer.current);
              playbackTimer.current = null;
              setPhase("playing");
            }}
          />
          {phase !== "playing" && <div className="camera-status">{message}</div>}
        </div>
        <footer className="camera-footer">
          <span><i className={`camera-dot ${tone}`} />{message}</span>
          {size && <span className="mono">{size.width}×{size.height}{selectedCamera?.fps ? ` · ${selectedCamera.fps}fps` : ""}</span>}
          {cameraId && <span className="mono">{cameraId}</span>}
        </footer>
      </section>
    </div>
  );
}
