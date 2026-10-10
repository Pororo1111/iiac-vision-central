"""학습·추론에 쓰는 로컬 AI 자산 경로. 파일은 Git 제외 .artifacts/ai-assets/ 에 둔다."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".artifacts/ai-assets"
BASE_MODEL = ASSETS / "models/ppe4-rf27-local-m-20260914.pt"
BASELINE = ASSETS / "datasets/ppe4-rf27-combined-20260914/dataset.yaml"
PRETRAINED = {"yolo26n": ASSETS / "models/yolo26n.pt", "yolo26m": ASSETS / "models/yolo26m.pt"}
ROBOFLOW = ASSETS / "datasets/roboflow"
# 학습 클래스 표. 기준 모델·기준 데이터셋은 앞 4개만 알며, 뒤 클래스는 2026-10-09에 추가한 현장 자체 장치다.
# 번호를 유지하려고 새 클래스는 항상 뒤에 붙인다. 앞부분이 같은 모델·데이터셋은 그대로 호환된다.
CLASSES = ["person", "safety_vest", "traffic_cone", "hard_hat", "signal_tower", "smart_cone"]


def compatible(names) -> bool:
    """CLASSES의 앞부분과 같은 클래스 표인지 확인한다(4클래스 기준 모델·이전 데이터셋 포함)."""
    names = list(names)
    return 0 < len(names) <= len(CLASSES) and names == CLASSES[:len(names)]
