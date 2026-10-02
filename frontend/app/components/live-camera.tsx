"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, csrfJson, type Device, type DeviceCamera } from "../../lib/api";

type ViewSession = { whepUrl: string; token: string; expiresAt: string };

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

export default function LiveCamera({ device, onClose }: { device: Device; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const peer = useRef<RTCPeerConnection | null>(null);
  const resource = useRef<string | null>(null);
  const token = useRef<string | null>(null);
  const playbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [cameras, setCameras] = useState<DeviceCamera[]>([]);
  const [cameraId, setCameraId] = useState("");
  const [state, setState] = useState("카메라 정보를 확인하고 있습니다…");
  const [error, setError] = useState("");

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

  const connect = useCallback(async (selected: string) => {
    disconnect();
    setError("");
    setState("보안 시청 세션을 준비하고 있습니다…");
    try {
      const session = await csrfJson<ViewSession>(
        `/devices/${encodeURIComponent(device.deviceId)}/cameras/${encodeURIComponent(selected)}/view-session`,
      );
      const pc = new RTCPeerConnection();
      peer.current = pc;
      token.current = session.token;
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.ontrack = (event) => {
        if (video.current) video.current.srcObject = event.streams[0];
        setState("첫 영상 프레임을 기다리고 있습니다…");
      };
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
          setState("첫 영상 프레임을 기다리고 있습니다…");
        }
        if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
          setError("영상 연결이 끊겼습니다. 다시 연결해 주세요.");
        }
      };
      await pc.setLocalDescription(await pc.createOffer());
      await waitForIce(pc);
      const response = await fetch(session.whepUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.token}`,
          "Content-Type": "application/sdp",
        },
        body: pc.localDescription?.sdp,
      });
      if (!response.ok) throw new Error(`WHEP_${response.status}`);
      const location = response.headers.get("Location");
      if (location) resource.current = new URL(location, session.whepUrl).toString();
      setState("영상 연결 중…");
      await pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
      playbackTimer.current = setTimeout(() => {
        if (!video.current || video.current.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
          disconnect();
          setState("");
          setError("영상 재생을 시작하지 못했습니다. 다시 연결해 주세요.");
        }
      }, 12000);
    } catch (reason) {
      disconnect();
      if (reason instanceof ApiError && reason.status === 409) {
        setError(reason.message === "DEVICE_OFFLINE" ? "장치가 오프라인입니다." : "카메라 송출을 준비 중입니다.");
      } else {
        setError("실시간 영상을 연결하지 못했습니다.");
      }
      setState("");
    }
  }, [device.deviceId, disconnect]);

  useEffect(() => {
    let active = true;
    api<DeviceCamera[]>(`/devices/${encodeURIComponent(device.deviceId)}/cameras`)
      .then((items) => {
        if (!active) return;
        setCameras(items);
        const first = items.find((item) => item.streamStatus === "STREAMING") || items[0];
        if (first) {
          setCameraId(first.cameraId);
          void connect(first.cameraId);
        } else {
          setState("");
          setError("등록된 카메라가 없습니다.");
        }
      })
      .catch(() => {
        if (active) setError("카메라 정보를 불러오지 못했습니다.");
      });
    return () => {
      active = false;
      disconnect();
    };
  }, [connect, device.deviceId, disconnect]);

  return (
    <div className="camera-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="camera-dialog" role="dialog" aria-modal="true" aria-labelledby="camera-title">
        <header className="camera-heading">
          <div>
            <p className="eyebrow">LIVE CAMERA</p>
            <h2 id="camera-title">{device.name}</h2>
            <span className="mono">{device.deviceId}</span>
          </div>
          <button onClick={onClose} aria-label="실시간 영상 닫기">닫기 ×</button>
        </header>
        {cameras.length > 1 && (
          <label className="camera-select">카메라
            <select value={cameraId} onChange={(event) => { setCameraId(event.target.value); void connect(event.target.value); }}>
              {cameras.map((camera) => <option key={camera.cameraId} value={camera.cameraId}>{camera.displayName}</option>)}
            </select>
          </label>
        )}
        <div className="camera-stage">
          <video
            ref={video}
            autoPlay
            muted
            playsInline
            controls
            onPlaying={() => {
              if (playbackTimer.current) clearTimeout(playbackTimer.current);
              playbackTimer.current = null;
              setError("");
              setState("실시간 영상");
            }}
            onWaiting={() => !error && setState("영상 버퍼링 중…")}
          />
          {!video.current?.srcObject && <div className="camera-status">{error || state}</div>}
        </div>
        <footer className="camera-footer">
          <span><i className={error ? "camera-dot bad" : "camera-dot"} />{error || state}</span>
          {cameraId && <span className="mono">{cameraId}</span>}
          {error && <button onClick={() => void connect(cameraId)} disabled={!cameraId}>다시 연결</button>}
        </footer>
      </section>
    </div>
  );
}
