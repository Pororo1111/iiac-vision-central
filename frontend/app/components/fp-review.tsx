"use client";
import { useEffect, useState } from "react";
import { api, ApiError, csrfJson } from "../../lib/api";

/** 오탐 확인(V33·V34): Gemma가 오탐·애매로 본 보호구 박스가 있는 원본 사진. 관리자가 박스별로 정해 승인하면 데이터셋에 쌓인다. */
export type Verdict = { index: number; cls: string; score: number; box: number[]; verdict: "real" | "false" | "unsure"; seen: string; reason: string };
export type ReviewItem = {
  item: {
    id: string;
    deviceId: string;
    cameraId: string;
    capturedAt: string;
    reviewStatus: "NOT_NEEDED" | "PENDING" | "APPROVED" | "REJECTED" | null;
    verdicts: string | null;
    falseCount: number | null;
    unsureCount: number | null;
    datasetSplit: string | null;
  };
  imagePath: string;
};
type Obj = { index: number; cls: string; score: number; box: number[] };
type Detail = ReviewItem & { width: number; height: number; objects: Obj[] };
type Dataset = { url: string; train: number; val: number; boxes: number };

export const clsNames: Record<string, string> = { hard_hat: "안전모", safety_vest: "안전조끼", person: "사람", traffic_cone: "콘" };
const verdictNames = { real: "정탐", false: "오탐", unsure: "애매" };
const statusNames = { PENDING: "확인 필요", APPROVED: "승인됨", REJECTED: "반려됨", NOT_NEEDED: "확인 불필요" };

export function verdictsOf(item: ReviewItem["item"]): Verdict[] {
  try {
    return item.verdicts ? (JSON.parse(item.verdicts) as Verdict[]) : [];
  } catch {
    return [];
  }
}

function stamp(value: string) {
  return new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/** 사진 위에 박스를 퍼센트 좌표로 그린다(박스는 0~1 정규화). */
function Boxes({ boxes }: { boxes: { key: string; box: number[]; kind: string; label?: string }[] }) {
  return (
    <>
      {boxes.map((b) => (
        <span key={b.key} className={`fpr-box ${b.kind}`}
          style={{ left: `${b.box[0] * 100}%`, top: `${b.box[1] * 100}%`, width: `${(b.box[2] - b.box[0]) * 100}%`, height: `${(b.box[3] - b.box[1]) * 100}%` }}>
          {b.label && <em>{b.label}</em>}
        </span>
      ))}
    </>
  );
}

export function ReviewCards({ items, onOpen }: { items: ReviewItem[]; onOpen: (id: string) => void }) {
  if (!items.length) return <div className="vsx-empty">관리자 확인이 필요한 사진이 없습니다.</div>;
  return (
    <div className="vsx-cards">
      {items.map(({ item, imagePath }) => {
        const verdicts = verdictsOf(item);
        return (
          <article key={item.id} className={`vsx-card fpr-card ${item.reviewStatus?.toLowerCase() ?? ""}`}>
            <header>
              <span title={item.deviceId}>{item.cameraId}</span>
              <span className={`fpr-status ${item.reviewStatus?.toLowerCase() ?? ""}`}>{statusNames[item.reviewStatus ?? "PENDING"]}</span>
            </header>
            <button className="fpr-thumb" onClick={() => onOpen(item.id)} aria-label={`${stamp(item.capturedAt)} 사진 확인`}>
              <img src={imagePath} alt="" loading="lazy" />
              <Boxes boxes={verdicts.map((v) => ({ key: String(v.index), box: v.box, kind: v.verdict }))} />
              <span className="vsx-time">{stamp(item.capturedAt)}</span>
            </button>
            <div className="vsx-tags">
              {verdicts.map((v) => (
                <span key={v.index} className={`fpr-tag ${v.verdict}`} title={v.reason}>
                  {clsNames[v.cls] ?? v.cls} {verdictNames[v.verdict]} · {v.seen}
                </span>
              ))}
            </div>
          </article>
        );
      })}
    </div>
  );
}

/** 박스별로 정탐(라벨 유지)·오탐(라벨에서 뺌)을 정해 승인하거나 사진 전체를 반려한다. 기본값은 Gemma 판정(애매는 직접 선택). */
export function ReviewDialog({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: (item: ReviewItem["item"]) => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [decisions, setDecisions] = useState<Record<number, "keep" | "remove" | undefined>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ status: string; dataset: Dataset } | null>(null);

  useEffect(() => {
    let active = true;
    api<Detail>(`/training/fp-checks/${id}`)
      .then((d) => {
        if (!active) return;
        setDetail(d);
        const initial: Record<number, "keep" | "remove" | undefined> = {};
        for (const v of verdictsOf(d.item)) initial[v.index] = v.verdict === "false" ? "remove" : v.verdict === "real" ? "keep" : undefined;
        setDecisions(initial);
      })
      .catch(() => active && setError("사진 정보를 불러오지 못했습니다."));
    return () => {
      active = false;
    };
  }, [id]);

  const verdicts = detail ? verdictsOf(detail.item) : [];
  const undecided = verdicts.filter((v) => !decisions[v.index]).length;
  const pending = detail?.item.reviewStatus === "PENDING" && !result;

  async function submit(action: "APPROVE" | "REJECT") {
    setBusy(true);
    setError("");
    try {
      const removed = verdicts.filter((v) => decisions[v.index] === "remove").map((v) => v.index);
      const r = await csrfJson<{ item: ReviewItem["item"]; dataset: Dataset }>(`/training/fp-checks/${id}/review`, { action, removed });
      setResult({ status: r.item.reviewStatus ?? "", dataset: r.dataset });
      onDone(r.item);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? "이미 처리된 사진입니다." : "처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const persons = detail?.objects.filter((o) => !verdicts.some((v) => v.index === o.index)) ?? [];
  return (
    <div className="camera-backdrop" role="dialog" aria-modal="true" aria-label="오탐 의심 사진 확인" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="fpr-dialog">
        <header>
          <div>
            <strong>오탐 의심 사진 확인</strong>
            {detail && <small>{detail.item.deviceId} / {detail.item.cameraId} · {stamp(detail.item.capturedAt)} · YOLO 원본 프레임</small>}
          </div>
          <button onClick={onClose} aria-label="닫기">✕</button>
        </header>
        {!detail && !error && <p className="muted">불러오는 중…</p>}
        {detail && (
          <div className="fpr-body">
            <div className="fpr-image">
              <img src={detail.imagePath} alt="원본 프레임" />
              <Boxes boxes={[
                ...persons.map((o) => ({ key: `p${o.index}`, box: o.box, kind: "other" })),
                ...verdicts.map((v) => ({ key: String(v.index), box: v.box, kind: decisions[v.index] === "remove" ? "false" : decisions[v.index] === "keep" ? "real" : "unsure", label: `#${v.index} ${clsNames[v.cls] ?? v.cls}` })),
              ]} />
            </div>
            <div className="fpr-side">
              <p className="muted">박스마다 정탐(라벨 유지) 또는 오탐(라벨에서 뺌)을 고르세요. 기본값은 Gemma 판정이며, 애매한 박스는 직접 골라야 합니다.</p>
              {verdicts.map((v) => (
                <div key={v.index} className="fpr-row">
                  <div>
                    <strong>#{v.index} {clsNames[v.cls] ?? v.cls}</strong> <small>YOLO {v.score.toFixed(2)}</small>
                    <p><span className={`fpr-tag ${v.verdict}`}>Gemma {verdictNames[v.verdict]}</span> {v.seen}</p>
                    <small className="muted">{v.reason}</small>
                  </div>
                  <div className="fpr-toggle" role="group" aria-label={`#${v.index} 판정`}>
                    <button disabled={!pending} className={decisions[v.index] === "keep" ? "on keep" : ""} onClick={() => setDecisions({ ...decisions, [v.index]: "keep" })}>정탐</button>
                    <button disabled={!pending} className={decisions[v.index] === "remove" ? "on remove" : ""} onClick={() => setDecisions({ ...decisions, [v.index]: "remove" })}>오탐</button>
                  </div>
                </div>
              ))}
              {persons.length > 0 && <small className="muted">사람·콘 박스 {persons.length}개는 YOLO 결과 그대로 라벨에 남습니다.</small>}
              {result ? (
                <p className="fpr-result">
                  {result.status === "APPROVED" ? "승인했습니다. 데이터셋에 추가했습니다." : "반려했습니다. 데이터셋에 넣지 않습니다."}
                  <br />누적 데이터셋 {result.dataset.url}: train {result.dataset.train} · val {result.dataset.val}
                </p>
              ) : !pending ? (
                <p className="fpr-result">{statusNames[detail.item.reviewStatus ?? "PENDING"]} 상태라 다시 처리할 수 없습니다.</p>
              ) : (
                <div className="fpr-actions">
                  <button className="primary" disabled={busy || undecided > 0} onClick={() => void submit("APPROVE")}>
                    {undecided ? `애매한 박스 ${undecided}개를 먼저 고르세요` : "승인하고 데이터셋에 추가"}
                  </button>
                  <button disabled={busy} onClick={() => void submit("REJECT")}>반려</button>
                </div>
              )}
              {error && <p className="vsx-error" role="alert">{error}</p>}
            </div>
          </div>
        )}
        {error && !detail && <p className="vsx-error" role="alert">{error}</p>}
      </div>
    </div>
  );
}
