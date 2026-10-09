"use client";
import { useEffect, useState } from "react";
import { api, type CameraSources, type DeviceCamera, type SizePeriod } from "../../lib/api";

/** 같은 camera_id(usb-0)에 다른 웹캠이 꽂히면 원본 크기·비율이 바뀐다. 송출 해상도와 원본을 구분해 보여 준다. */

function ratio(w: number, h: number) {
  const r = w / h;
  if (Math.abs(r - 16 / 9) < 0.02) return "16:9";
  if (Math.abs(r - 4 / 3) < 0.02) return "4:3";
  return r.toFixed(2) + ":1";
}

function day(value: string) {
  return new Date(value).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function Periods({ items, empty }: { items: SizePeriod[]; empty: string }) {
  if (!items.length) return <span className="muted">{empty}</span>;
  const [latest, ...older] = items;
  return (
    <>
      <strong>
        {latest.width}×{latest.height} <span className="muted">({ratio(latest.width, latest.height)})</span>
      </strong>
      <small>
        {older.length ? `${day(latest.firstAt)}부터` : `${day(latest.firstAt)} ~ ${day(latest.lastAt)}`} · {latest.count}장
      </small>
      {older.length > 0 && (
        <small className="camera-source-history">
          이전:{" "}
          {older
            .slice(0, 3)
            .map((p) => `${p.width}×${p.height} (${day(p.firstAt)} ~ ${day(p.lastAt)}, ${p.count}장)`)
            .join(" · ")}
        </small>
      )}
    </>
  );
}

export default function CameraSourcesPanel({ deviceId }: { deviceId: string }) {
  const [rows, setRows] = useState<{ camera: DeviceCamera; sources: CameraSources | null }[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const base = `/devices/${encodeURIComponent(deviceId)}/cameras`;
    async function load() {
      try {
        const cameras = await api<DeviceCamera[]>(base);
        const sources = await Promise.all(
          cameras.map((c) => api<CameraSources>(`${base}/${encodeURIComponent(c.cameraId)}/sources`).catch(() => null)),
        );
        if (!active) return;
        setRows(cameras.map((camera, i) => ({ camera, sources: sources[i] })));
        setError("");
      } catch {
        if (active) setError("카메라 정보를 받지 못했습니다.");
      } finally {
        if (active) timer = setTimeout(load, 30000);
      }
    }
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [deviceId]);

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>카메라 원본</h2>
          <p>
            송출 해상도는 IoT가 중앙으로 보내는 영상 크기이고 원본과 다를 수 있습니다. 원본 사진 크기가 바뀐 시점을 웹캠 교체
            시점으로 볼 수 있습니다(사람이 감지될 때만 사진이 오므로 대략적인 시점입니다).
          </p>
        </div>
        <span className="small-label">CAMERA</span>
      </div>
      {error && <p className="error">{error}</p>}
      {!rows ? (
        <p className="muted">카메라 정보를 불러오는 중…</p>
      ) : !rows.length ? (
        <p className="muted">보고된 카메라가 없습니다.</p>
      ) : (
        <div className="camera-sources">
          {rows.map(({ camera: c, sources: s }) => (
            <article key={c.cameraId}>
              <h3 className="mono">{c.cameraId}</h3>
              <dl>
                <div>
                  <dt>원본 장치 (IoT 보고)</dt>
                  <dd>
                    {c.sourceWidth && c.sourceHeight ? (
                      <>
                        <strong>
                          {c.sourceWidth}×{c.sourceHeight}{" "}
                          <span className="muted">({ratio(c.sourceWidth, c.sourceHeight)})</span>
                        </strong>
                        <small>{c.sourceDevice || "장치 이름 없음"}</small>
                        {s && s.reported.length > 1 && (
                          <small className="camera-source-history">
                            이전:{" "}
                            {s.reported
                              .slice(1, 4)
                              .map((r) => `${r.sourceDevice || "이름 없음"} ${r.sourceWidth}×${r.sourceHeight} (~${day(r.lastSeenAt)})`)
                              .join(" · ")}
                          </small>
                        )}
                      </>
                    ) : (
                      <span className="muted">IoT가 아직 보고하지 않음</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>원본 사진 (사람 감지)</dt>
                  <dd>{s ? <Periods items={s.snapshots} empty="받은 사진 없음" /> : <span className="muted">정보 없음</span>}</dd>
                </div>
                <div>
                  <dt>수집 이미지 (학습용)</dt>
                  <dd>{s ? <Periods items={s.samples} empty="수집 이미지 없음" /> : <span className="muted">정보 없음</span>}</dd>
                </div>
                <div>
                  <dt>송출 해상도</dt>
                  <dd>
                    {c.width && c.height ? (
                      <strong>
                        {c.width}×{c.height}
                        {c.fps ? ` · ${c.fps}fps` : ""}
                      </strong>
                    ) : (
                      <span className="muted">보고 없음</span>
                    )}
                    <small>마지막 송출 {c.lastStreamAt ? day(c.lastStreamAt) : "없음"}</small>
                  </dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
