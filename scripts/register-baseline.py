"""기존 모델·데이터셋을 읽기 전용으로 확인하고 로컬 기준 목록을 만든다."""
import hashlib
import json
from pathlib import Path
import yaml

from ai_assets import BASE_MODEL as WEIGHTS, BASELINE as DATASET, ROOT

CLASSES = ['person', 'safety_vest', 'traffic_cone', 'hard_hat']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    config = yaml.safe_load(DATASET.read_text())
    if config['names'] != CLASSES:
        raise SystemExit('기준 클래스가 일치하지 않습니다.')
    base = Path(config['path'])
    splits = {}
    for split in ['train', 'val', 'test']:
        directory = base / config[split]
        if not directory.is_dir():
            raise SystemExit('데이터셋 분할 경로가 없습니다: ' + split)
        images = sorted(p for p in directory.iterdir() if p.suffix.lower() in {'.jpg', '.jpeg', '.png'})
        entries = []
        for image in images:
            label = base / 'labels' / split / (image.stem + '.txt')
            if not label.is_file():
                raise SystemExit('라벨 파일이 없습니다: ' + str(label))
            for line in label.read_text().splitlines():
                values = line.split()
                if len(values) != 5 or values[0] not in {'0', '1', '2', '3'} or not all(0 <= float(v) <= 1 for v in values[1:]):
                    raise SystemExit('라벨 형식을 확인하세요: ' + str(label))
            entries.append({'image': str(image), 'imageSha256': digest(image), 'label': str(label), 'labelSha256': digest(label)})
        splits[split] = entries
    manifest = {'name': 'ppe4-rf27-combined-20260914', 'weights': str(WEIGHTS), 'weightsSha256': digest(WEIGHTS),
        'dataset': str(DATASET), 'datasetSha256': digest(DATASET), 'classes': CLASSES, 'splits': splits,
        'note': '읽기 전용 기준 목록. 학습 데이터셋 조합·스냅샷 생성은 아직 구현하지 않음.'}
    out = ROOT / '.artifacts/baseline.json'
    out.parent.mkdir(exist_ok=True)
    temporary = out.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    temporary.replace(out)
    print('기준 모델·데이터셋 등록:', {key: len(value) for key, value in splits.items()})


if __name__ == '__main__':
    main()
