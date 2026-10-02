"""학습·추론에 쓰는 로컬 AI 자산 경로. 파일은 Git 제외 .artifacts/ai-assets/ 에 둔다."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".artifacts/ai-assets"
BASE_MODEL = ASSETS / "models/ppe4-rf27-local-m-20260914.pt"
BASELINE = ASSETS / "datasets/ppe4-rf27-combined-20260914/dataset.yaml"
PRETRAINED = {"yolo26n": ASSETS / "models/yolo26n.pt", "yolo26m": ASSETS / "models/yolo26m.pt"}
ROBOFLOW = ASSETS / "datasets/roboflow"
