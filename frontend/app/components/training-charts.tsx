"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";

export type Metrics = { columns: string[]; rows: number[][] };
type Series = { name: string; values: number[]; color: string };
// 검증한 범주 팔레트 1~3번(파랑·주황·청록). 차트 하나에 최대 3개 선만 쓴다.
const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const W = 320, H = 170, PAD = { left: 44, right: 14, top: 12, bottom: 26 };

const label = (column: string) => column.replace(/^(train|val|metrics|lr)\//, "").replace("(B)", "").replace("_loss", " loss");
const format = (value: number) => Math.abs(value) >= 100 ? value.toFixed(0) : Math.abs(value) >= 1 ? value.toFixed(3) : value < 0.001 && value > 0 ? value.toExponential(1) : value.toFixed(4);
export const duration = (seconds: number) => seconds >= 3600 ? `${Math.floor(seconds / 3600)}시간 ${Math.round(seconds % 3600 / 60)}분` : seconds >= 60 ? `${Math.round(seconds / 60)}분` : `${Math.round(seconds)}초`;

/** 에포크별 지표 선 차트. 새 에포크가 들어오면 마지막 구간만 그려지는 애니메이션과 끝점 맥박을 보여 준다. */
function LineChart({ title, epochs, series, total, live }: { title: string; epochs: number[]; series: Series[]; total: number; live: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  const all = series.flatMap(s => s.values).filter(Number.isFinite);
  let min = Math.min(...all), max = Math.max(...all);
  if (!all.length) { min = 0; max = 1; }
  if (min === max) { min -= Math.abs(min) * 0.1 || 0.1; max += Math.abs(max) * 0.1 || 0.1; }
  const pad = (max - min) * 0.1; min -= pad; max += pad;
  const lastEpoch = Math.max(total, epochs[epochs.length - 1] ?? 1, 2);
  const x = (epoch: number) => PAD.left + (epoch - 1) / (lastEpoch - 1) * (W - PAD.left - PAD.right);
  const y = (value: number) => PAD.top + (1 - (value - min) / (max - min)) * (H - PAD.top - PAD.bottom);
  const ticks = [min + pad, (min + max) / 2, max - pad];
  const n = epochs.length;
  const move = (event: React.MouseEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const epoch = 1 + ((event.clientX - box.left) / box.width * W - PAD.left) / (W - PAD.left - PAD.right) * (lastEpoch - 1);
    let best = 0;
    epochs.forEach((value, index) => { if (Math.abs(value - epoch) < Math.abs(epochs[best] - epoch)) best = index; });
    setHover(n ? best : null);
  };
  return <figure className="metric-chart">
    <figcaption><strong>{title}</strong>
      {series.length > 1 && <span className="metric-legend">{series.map(s => <span key={s.name}><i style={{ background: s.color }} />{s.name} {n ? format(s.values[n - 1]) : "—"}</span>)}</span>}
      {series.length === 1 && n > 0 && <span className="metric-legend">{format(series[0].values[n - 1])}</span>}
    </figcaption>
    <div className="metric-plot">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title} 에포크별 추이`}>
        {ticks.map((tick, index) => <g key={index}><line className="metric-grid" x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} /><text className="metric-axis" x={PAD.left - 6} y={y(tick) + 3} textAnchor="end">{format(tick)}</text></g>)}
        {[...new Set([1, Math.round((1 + lastEpoch) / 2), lastEpoch])].map(epoch => <text key={epoch} className="metric-axis" x={x(epoch)} y={H - 8} textAnchor="middle">{epoch}</text>)}
        {series.map(s => {
          const points = epochs.map((epoch, index) => `${x(epoch).toFixed(1)},${y(s.values[index]).toFixed(1)}`);
          return <g key={s.name}>
            {n > 2 && <polyline className="metric-line" points={points.slice(0, n - 1).join(" ")} style={{ stroke: s.color }} />}
            {n > 1 && <polyline key={`seg-${n}`} className="metric-line metric-segment" pathLength={1} points={points.slice(n - 2).join(" ")} style={{ stroke: s.color }} />}
            {n > 0 && <>
              {live && <circle key={`pulse-${n}`} className="metric-pulse" cx={x(epochs[n - 1])} cy={y(s.values[n - 1])} r={4} style={{ stroke: s.color }} />}
              <circle className="metric-end" cx={x(epochs[n - 1])} cy={y(s.values[n - 1])} r={4} style={{ fill: s.color }} />
            </>}
          </g>;
        })}
        {hover !== null && <line className="metric-crosshair" x1={x(epochs[hover])} x2={x(epochs[hover])} y1={PAD.top} y2={H - PAD.bottom} />}
        <rect x={PAD.left - 8} y={0} width={W - PAD.left - PAD.right + 16} height={H} fill="transparent" onMouseMove={move} onMouseLeave={() => setHover(null)} />
      </svg>
      {hover !== null && <div className="metric-tooltip" style={{ left: `${x(epochs[hover]) / W * 100}%` }}>
        <strong>Epoch {epochs[hover]}</strong>{series.map(s => <span key={s.name}><i style={{ background: s.color }} />{s.name} {format(s.values[hover])}</span>)}
      </div>}
    </div>
  </figure>;
}

/** 학습 실행의 에포크 진행률과 지표 차트 묶음. live이면 5초마다 새로 읽는다. */
export default function TrainingCharts({ runId, total, live }: { runId: string; total: number; live: boolean }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { const data = await api<Metrics>(`/training/runs/${runId}/metrics`, { signal: controller.signal }); if (!controller.signal.aborted) setMetrics(data); } catch { /* 목록 화면의 오류 표시를 따른다. */ }
      if (live && !controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId, live]);
  const groups = useMemo(() => {
    if (!metrics?.columns.length) return [];
    const column = (name: string) => metrics.columns.indexOf(name);
    const pick = (title: string, names: string[]) => ({ title, series: names.filter(name => column(name) >= 0).slice(0, 3).map((name, index) => ({ name: label(name), color: COLORS[index], values: metrics.rows.map(row => row[column(name)]) })) });
    return [
      pick("mAP", ["metrics/mAP50(B)", "metrics/mAP50-95(B)"]),
      pick("정밀도 · 재현율", ["metrics/precision(B)", "metrics/recall(B)"]),
      pick("학습 손실", metrics.columns.filter(c => c.startsWith("train/"))),
      pick("검증 손실", metrics.columns.filter(c => c.startsWith("val/"))),
    ].filter(group => group.series.length);
  }, [metrics]);
  const epochIndex = metrics?.columns.indexOf("epoch") ?? -1;
  const timeIndex = metrics?.columns.indexOf("time") ?? -1;
  const epochs = metrics && epochIndex >= 0 ? metrics.rows.map(row => row[epochIndex]) : [];
  const done = epochs[epochs.length - 1] ?? 0;
  const elapsed = metrics && timeIndex >= 0 && metrics.rows.length ? metrics.rows[metrics.rows.length - 1][timeIndex] : 0;
  const remaining = done > 0 && total > done ? elapsed / done * (total - done) : 0;
  const percent = total ? Math.min(100, done / total * 100) : 0;
  return <section className="training-charts" aria-label="학습 지표">
    <div className="epoch-progress">
      <div><strong>Epoch {done} / {total}</strong><span className="muted">{elapsed ? ` · 경과 ${duration(elapsed)}` : ""}{live && remaining ? ` · 남은 시간 약 ${duration(remaining)}` : ""}</span></div>
      <div className={`epoch-bar ${live ? "live" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}><span style={{ width: `${percent}%` }} /></div>
    </div>
    {groups.length === 0 ? <div className="metric-grid-wrap">{["mAP", "정밀도 · 재현율", "학습 손실", "검증 손실"].map(title => <figure key={title} className="metric-chart metric-skeleton"><figcaption><strong>{title}</strong></figcaption><div className="metric-shimmer" /></figure>)}
      <p className="muted">{live ? "첫 epoch가 끝나면 지표가 나타납니다." : "기록된 학습 지표가 없습니다."}</p></div>
      : <div className="metric-grid-wrap">{groups.map(group => <LineChart key={group.title} title={group.title} epochs={epochs} series={group.series} total={total} live={live} />)}</div>}
    {metrics && metrics.rows.length > 0 && <details className="metric-table"><summary>표로 보기</summary><div className="table-scroll"><table><thead><tr>{metrics.columns.map(c => <th key={c}>{label(c)}</th>)}</tr></thead>
      <tbody>{metrics.rows.slice(-20).map((row, index) => <tr key={index}>{row.map((value, i) => <td key={i}>{format(value)}</td>)}</tr>)}</tbody></table></div></details>}
  </section>;
}
