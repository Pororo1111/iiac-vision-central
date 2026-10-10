"""오탐 판단 기억(V37, RAG): 사람이 정탐·오탐을 판단한 보호구 박스를 CLIP 이미지 임베딩으로 기억하고 새 박스와 비교한다.

- 임베딩: open_clip ViT-B-16(laion2b_s34b_b88k, 512차원, 정규화). 가중치는 .artifacts/ai-assets/embeddings에 둔다.
- 입력은 fp_check.crop_region과 같은 확대 영역이다(Gemma가 보는 확대 사진과 같다).
- 결정 규칙: 같은 클래스에서 가장 비슷한 정탐 사례와 오탐 사례의 유사도 차이가 MARGIN 이상이면 더 비슷한 쪽 판단을 따른다.
  한쪽 사례만 있으면 SOLO 이상일 때만 따른다. 그 밖에는 비슷한 사례를 예시로 붙여 Gemma가 판정한다(fp_check.py).
  사전 실험(2026-10-09, .artifacts/rag-exp-20261009): Claude 판별 사례 88건으로 10/08 오후 평가 26건 중 25건을 결정, 25건 모두 정답.
"""
import base64
import io
import threading

from worker import ROOT, api

MODEL = 'ViT-B-16'
PRETRAINED = 'laion2b_s34b_b88k'
NAME = f'open_clip:{MODEL}:{PRETRAINED}'
MARGIN = 0.03
SOLO = 0.9
_lock = threading.Lock()
_model = None


def _load():
    global _model
    with _lock:
        if _model is None:
            import open_clip
            import torch
            model, _, preprocess = open_clip.create_model_and_transforms(
                MODEL, pretrained=PRETRAINED, cache_dir=str(ROOT / '.artifacts' / 'ai-assets' / 'embeddings'))
            model.eval()
            _model = (model, preprocess, torch)
    return _model


def embed(crop):
    """확대 사진 → 정규화한 512차원 목록."""
    model, preprocess, torch = _load()
    with torch.no_grad():
        vector = model.encode_image(preprocess(crop.convert('RGB')).unsqueeze(0))
        vector = torch.nn.functional.normalize(vector, dim=-1)[0]
    return [round(float(v), 7) for v in vector]


def jpeg(crop):
    image = crop.convert('RGB').copy()
    image.thumbnail((512, 512))
    data = io.BytesIO()
    image.save(data, 'JPEG', quality=88)
    return base64.b64encode(data.getvalue()).decode()


def search(cls, vector, k=4):
    """정탐·오탐 판단마다 가장 비슷한 k개(유사도 높은 순으로 합친 목록)."""
    return api('/fp-memory/search', {'cls': cls, 'vector': vector, 'k': k}) or []


def decide(matches):
    """(label, best, opposite) 또는 None. best는 따른 사례, opposite는 반대 판단 사례 중 가장 비슷한 것(없으면 None)."""
    best = {}
    for m in matches:
        if m['label'] not in best or m['similarity'] > best[m['label']]['similarity']:
            best[m['label']] = m
    if len(best) == 2:
        real, false = best['real'], best['false']
        if abs(real['similarity'] - false['similarity']) < MARGIN:
            return None
        top, other = (real, false) if real['similarity'] > false['similarity'] else (false, real)
        return top['label'], top, other
    if len(best) == 1:
        only = next(iter(best.values()))
        if only['similarity'] >= SOLO:
            return only['label'], only, None
    return None


def fill_pending():
    """관리자가 방금 승인한 박스의 임베딩을 채운다. 처리한 수를 돌려준다."""
    from PIL import Image
    from fp_check import crop_region
    done = 0
    for item in api('/fp-memory/pending') or []:
        image = Image.open(item['imagePath']).convert('RGB')
        crop = image.crop(crop_region(item['cls'], item['box'], *image.size))
        api('/fp-memory/' + item['id'] + '/embedding', {'vector': embed(crop), 'model': NAME, 'crop': jpeg(crop)})
        done += 1
    if done:
        print('오탐 판단 기억 임베딩', done, '건', flush=True)
    return done
