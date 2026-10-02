export const STAGES = [
  { key: "collect", title: "데이터 수집", hint: "IoT · 업로드 · 유튜브" },
  { key: "label", title: "자동 라벨링", hint: "Gemma 설명·태그" },
  { key: "dataset", title: "데이터셋 구성", hint: "YOLO 선별 + 로보플로우" },
  { key: "train", title: "학습 · 평가", hint: "베이스 모델 이어학습" },
  { key: "deploy", title: "모델 배포", hint: "IoT 장치로 전송" },
] as const;

/** 파이프라인 흐름을 순서대로 불이 켜지는 애니메이션으로 보여 주는 데모. 실제 데이터와 무관하다. */
export default function PipelineDemo() {
  return <section className="pipeline-demo" aria-label="파이프라인 흐름 미리보기">
    <div className="pipeline-demo-head"><span className="eyebrow">미리보기</span><strong>새로운 파이프라인은 이렇게 흘러갑니다</strong></div>
    <ol className="pipeline-demo-track">
      {STAGES.map((stage, index) => <li key={stage.key} className="pipeline-demo-node" style={{ ["--i" as string]: index }}>
        <span className="pipeline-demo-dot" aria-hidden="true"><span className="pipeline-demo-check">✓</span></span>
        <strong>{stage.title}</strong><small>{stage.hint}</small>
        {index < STAGES.length - 1 && <span className="pipeline-demo-link" aria-hidden="true"><span /></span>}
      </li>)}
    </ol>
    <p className="muted">수집 방식과 학습 옵션만 고르면 라벨링·데이터셋 구성은 자동으로 끝나고, 평가를 통과한 모델은 목록에서 바로 배포할 수 있습니다.</p>
  </section>;
}
