"""포즈 키포인트 기반 쓰러짐 판정 규칙(참고 구현).

IoT(Jetson Orin Nano)가 yolo26n-pose 결과로 같은 규칙을 돌리도록 만든 기준 코드다.
표준 라이브러리만 쓰므로 그대로 복사하거나 옮겨 쓸 수 있다.

입력: 프레임마다 시각(초)과 사람 목록 {id, box[x1,y1,x2,y2], kp[[x,y,conf]*17]} (COCO 순서).
판정(옆·비스듬한 시점 전용, 천장 수직 시점은 미지원):
1. 자세: 키포인트 가로/세로 폭 비율 또는 박스 가로세로비가 기준 이상이면 누움, 둘 다 낮으면 서 있음.
2. 전이: 서 있던 사람이 `transition_seconds` 안에 누움으로 바뀌면 후보(천천히 눕기·처음부터 누운 사람 제외).
3. 확정: 후보가 `hold_seconds` 동안 누움을 유지하면 FALL_START. 누운 사람을 포즈 모델이 놓쳐도
   `lost_seconds`까지 후보를 유지하고, 추적 ID가 바뀌어도 박스가 겹치면 같은 후보로 본다.
4. 종료: 확정 뒤 서 있음이 `recover_seconds` 이어지거나 `lost_seconds` 넘게 안 보이면 FALL_END.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FallConfig:
    kp_conf: float = 0.3          # 키포인트 사용 최소 신뢰도
    min_keypoints: int = 5        # 키포인트 비율을 쓰는 최소 개수(미만이면 박스 비율만)
    lying_kp_ratio: float = 1.2   # 키포인트 가로/세로 ≥ 이면 누움
    lying_box_ratio: float = 1.1  # 박스 가로/세로 ≥ 이면 누움
    upright_ratio: float = 0.8    # 두 비율이 모두 < 이면 서 있음(그 사이는 애매)
    transition_seconds: float = 1.5
    hold_seconds: float = 2.0
    lying_fraction: float = 0.6   # 후보 기간 관측 중 누움 비율
    min_observations: int = 3
    recover_seconds: float = 2.0
    lost_seconds: float = 5.0
    match_iou: float = 0.2


def posture(person: dict, cfg: FallConfig) -> str:
    """'lying' | 'upright' | 'unknown'."""
    x1, y1, x2, y2 = person["box"]
    box_ratio = (x2 - x1) / max(y2 - y1, 1e-6)
    pts = [(x, y) for x, y, c in person.get("kp") or [] if c >= cfg.kp_conf]
    kp_ratio = None
    if len(pts) >= cfg.min_keypoints:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        kp_ratio = (max(xs) - min(xs)) / max(max(ys) - min(ys), 1e-6)
    if box_ratio >= cfg.lying_box_ratio or (kp_ratio is not None and kp_ratio >= cfg.lying_kp_ratio):
        return "lying"
    if box_ratio < cfg.upright_ratio and (kp_ratio is None or kp_ratio < cfg.upright_ratio):
        return "upright"
    return "unknown"


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class _Candidate:
    track_id: object
    started: float           # 누움으로 바뀐 시각
    box: list
    last_seen: float
    lying: int = 1
    observed: int = 1
    confirmed: bool = False
    upright_since: float | None = None


class FallDetector:
    """카메라 하나당 하나. update()가 이번 프레임에 생긴 이벤트 목록을 돌려준다."""

    def __init__(self, cfg: FallConfig | None = None):
        self.cfg = cfg or FallConfig()
        self.last_upright: dict[object, tuple[float, list]] = {}
        self.candidates: list[_Candidate] = []

    def update(self, t: float, persons: list[dict]) -> list[dict]:
        cfg = self.cfg
        events: list[dict] = []
        used: set[int] = set()
        for p in persons:
            state = posture(p, cfg)
            tid = p.get("id")
            cand = self._match(p, used, by_box=state != "upright")
            if state == "upright":
                if tid is not None:
                    self.last_upright[tid] = (t, p["box"])
                if cand is not None:
                    cand.observed += 1
                    cand.last_seen, cand.box = t, p["box"]
                    if cand.upright_since is None:
                        cand.upright_since = t
            elif state == "lying":
                if cand is not None:
                    cand.observed += 1
                    cand.lying += 1
                    cand.last_seen, cand.box, cand.upright_since = t, p["box"], None
                    if tid is not None:
                        cand.track_id = tid
                else:
                    if self._was_upright(tid, p["box"], t):
                        self.candidates.append(_Candidate(tid, t, p["box"], t))
                        used.add(len(self.candidates) - 1)
            elif cand is not None:
                cand.observed += 1
                cand.last_seen, cand.box = t, p["box"]

        for c in list(self.candidates):
            if not c.confirmed:
                if c.upright_since is not None or t - c.last_seen > cfg.lost_seconds:
                    self.candidates.remove(c)
                elif (t - c.started >= cfg.hold_seconds and c.observed >= cfg.min_observations
                      and c.lying / c.observed >= cfg.lying_fraction):
                    c.confirmed = True
                    events.append({"type": "FALL_START", "at": t, "fallStartedAt": c.started,
                                   "trackId": c.track_id, "box": c.box})
            else:
                recovered = c.upright_since is not None and t - c.upright_since >= cfg.recover_seconds
                if recovered or t - c.last_seen > cfg.lost_seconds:
                    self.candidates.remove(c)
                    events.append({"type": "FALL_END", "at": t, "trackId": c.track_id,
                                   "reason": "recovered" if recovered else "lost"})
        horizon = t - max(cfg.transition_seconds, cfg.lost_seconds)
        self.last_upright = {k: v for k, v in self.last_upright.items() if v[0] >= horizon}
        return events

    def _was_upright(self, tid, box, t: float) -> bool:
        """같은 추적 ID, 없으면 박스가 겹친 사람이 전이 시간 안에 서 있었는지(쓰러질 때 ID가 바뀌는 경우)."""
        recent = {k: v for k, v in self.last_upright.items() if t - v[0] <= self.cfg.transition_seconds}
        if tid in recent:
            return True
        # 쓰러지면 몸이 서 있던 자리에서 키만큼 옆으로 눕는다. 서 있던 박스를 좌우로 키만큼, 아래로 20% 넓혀 겹침을 본다.
        for _, (x1, y1, x2, y2) in recent.values():
            h = y2 - y1
            if iou(box, [x1 - h, y1, x2 + h, y2 + 0.2 * h]) > 0:
                return True
        return False

    def _match(self, p: dict, used: set[int], by_box: bool = True) -> _Candidate | None:
        best, best_i, best_score = None, None, 0.0
        for i, c in enumerate(self.candidates):
            if i in used:
                continue
            if p.get("id") is not None and p.get("id") == c.track_id:
                best, best_i = c, i
                break
            if not by_box:
                continue
            score = iou(p["box"], c.box)
            if score >= self.cfg.match_iou and score > best_score:
                best, best_i, best_score = c, i, score
        if best_i is not None:
            used.add(best_i)
        return best
