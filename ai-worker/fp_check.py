"""오탐 확인(V33·V34): 장치 원본 프레임에서 YOLO가 안전모·안전조끼로 잡은 박스를 Gemma가 하나씩 다시 본다.

Gemma는 판정(real·false·unsure)만 하고 데이터셋에는 쓰지 않는다. 오탐·애매가 있으면 중앙이 관리자 확인 대기(PENDING)로 두고,
관리자가 영상 검색 화면에서 박스별로 정탐·오탐을 정해 승인하면 중앙이 누적 데이터셋 local:admin-fp에 넣는다.
2026-10-07 검증에서 Gemma가 남색 공항 조끼·실제 안전모를 오탐으로 확정해, 사람 확인 없이 데이터셋에 넣지 않기로 했다.
observer.py가 질문·구간 분석이 없을 때 이 작업을 처리한다.
"""
import json
import time

from PIL import Image, ImageDraw

from worker import ROOT, api, encoded

PROMPT = 'fp-v4'
PPE = {'hard_hat': '안전모', 'safety_vest': '안전조끼'}
# 사람이 확인한 현장 예시(scripts/build-fp-references.py). 글 설명만으로는 Gemma가 공항 남색 작업 조끼를 일반 재킷으로 보므로
# 판정마다 같은 클래스의 예시를 함께 보여 준다(2026-10-07 검증).
REFERENCES = ROOT / '.artifacts' / 'ai-assets' / 'fp-references'
SYSTEM = (
    '당신은 산업 현장 보호구 탐지 결과를 검수하는 사람입니다. 첫 이미지는 장면 전체이고 빨간 박스가 검수 대상입니다. '
    '두 번째 이미지는 그 부분을 확대한 것입니다. 객체 탐지 모델이 이 박스를 보호구로 판단했는데 맞는지 판정하세요.\n'
    '안전모(hard_hat): 단단한 외피의 산업용 안전모만 real입니다. 머리에 쓰지 않고 손에 든 실제 안전모도 real입니다. '
    '대머리·머리카락·야구모자·비니·두건·후드·헤드폰·조명·사물은 false입니다.\n'
    '안전조끼(safety_vest): 형광(노랑·주황·연두) 조끼, 반사띠가 있는 조끼, 공항 작업자의 남색 작업 조끼(Incheon Airport 표기 등)는 real입니다. '
    '몸통에 반사띠(밝은 형광·은색 줄무늬)가 보이면 바탕색(남색·검정 포함)과 관계없이 real입니다. '
    '반사띠가 없는 일반 셔츠·와이셔츠·반팔 셔츠·티셔츠·나시(민소매)·니트 조끼·재킷·점퍼·앞치마·가방은 false입니다.\n'
    '박스가 너무 작거나 흐리거나 가려져 판단할 수 없으면, 또는 확신이 없으면 unsure입니다. 추측으로 real·false를 고르지 마세요.\n'
    'seen에는 박스 안의 실제 물체를 짧게(예: 대머리, 베이지 반팔 셔츠, 노란 형광 조끼), reason에는 근거를 한 문장으로 쓰세요.')
# 오탐(false) 판정은 YOLO 판단을 알려 주지 않고 다시 물어 두 번 모두 '없음'일 때만 확정한다. 잘못된 오탐 확정은
# 실제 보호구를 배경으로 가르치므로, 엇갈리면 unsure로 두고 데이터셋에 넣지 않는다.
CONFIRM_HAT = '빨간 박스 위치에 단단한 외피의 산업용 안전모가 있습니까? (대머리·머리카락·일반 모자는 안전모가 아닙니다)'
CONFIRM_SCHEMA = {'type': 'object', 'properties': {
    'answer': {'type': 'string', 'enum': ['yes', 'no', 'unsure']}, 'reason': {'type': 'string'}},
    'required': ['answer', 'reason'], 'additionalProperties': False}
# 조끼는 색만으로는 공항 남색 작업 조끼를 일반 옷과 구분하지 못한다(2026-10-07 검증). 그래서 구조를 묻고,
# 반사띠·형광색·덧입은 민소매 조끼가 모두 '아니오'일 때만 오탐으로 확정한다. 남색 조끼처럼 덧입은 조끼는 unsure로 남는다.
CONFIRM_VEST = ('빨간 박스 안 사람의 상체 옷을 보고 각각 yes·no·unsure로 답하세요. '
                'reflective: 몸통에 반사띠(밝은 형광·은색 줄무늬)가 있습니까? fluorescent: 옷이 형광색(노랑·주황·연두)입니까? '
                'layeredVest: 다른 상의 위에 덧입은 민소매 조끼입니까(조끼 소매 구멍으로 안쪽 옷 소매가 보임)?')
VEST_SCHEMA = {'type': 'object', 'properties': {
    **{k: {'type': 'string', 'enum': ['yes', 'no', 'unsure']} for k in ('reflective', 'fluorescent', 'layeredVest')},
    'reason': {'type': 'string'}}, 'required': ['reflective', 'fluorescent', 'layeredVest', 'reason'], 'additionalProperties': False}
SCHEMA = {'type': 'object', 'properties': {
    'verdict': {'type': 'string', 'enum': ['real', 'false', 'unsure']},
    'seen': {'type': 'string'}, 'reason': {'type': 'string'}},
    'required': ['verdict', 'seen', 'reason'], 'additionalProperties': False}


def pixel_box(box, width, height):
    x1, y1, x2, y2 = box
    return x1 * width, y1 * height, x2 * width, y2 * height


def crop_region(cls, box, width, height):
    """안전모는 얼굴·어깨까지, 조끼는 몸통 주변까지 넓혀 자른다. 너무 작은 박스도 최소 160px 영역을 본다."""
    x1, y1, x2, y2 = pixel_box(box, width, height)
    w, h = x2 - x1, y2 - y1
    if cls == 'hard_hat':
        x1, x2, y1, y2 = x1 - 0.8 * w, x2 + 0.8 * w, y1 - 0.5 * h, y2 + 2.0 * h
    else:
        x1, x2, y1, y2 = x1 - 0.3 * w, x2 + 0.3 * w, y1 - 0.3 * h, y2 + 0.2 * h
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half_w, half_h = max(80, (x2 - x1) / 2), max(80, (y2 - y1) / 2)
    return (max(0, int(cx - half_w)), max(0, int(cy - half_h)), min(width, int(cx + half_w)), min(height, int(cy + half_h)))


def views(image, obj):
    """장면 전체(빨간 박스 표시)와 확대 이미지."""
    width, height = image.size
    context = image.copy()
    draw = ImageDraw.Draw(context)
    draw.rectangle(pixel_box(obj['box'], width, height), outline=(255, 0, 0), width=max(3, width // 200))
    crop = image.crop(crop_region(obj['cls'], obj['box'], width, height))
    scale = 512 / max(crop.size)
    if scale > 1:
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
    return [encoded(context), encoded(crop)]


def references(cls):
    """(설명 문장, 인코딩 이미지 목록). 기준 이미지가 없으면 빈 값이다."""
    manifest = REFERENCES / 'manifest.json'
    if not manifest.exists():
        return '', []
    items = [r for r in json.loads(manifest.read_text()) if r['cls'] == cls]
    if not items:
        return '', []
    lines = [f"{i + 3}번째 이미지: {r['note']}" for i, r in enumerate(items)]
    text = ('\n3번째 이미지부터는 이 현장에서 사람이 확인한 참고 예시입니다(판정 대상 아님). '
            '판정 대상이 어느 예시와 같은 종류인지 비교하세요.\n' + '\n'.join(lines))
    return text, [encoded(Image.open(REFERENCES / r['file']).convert('RGB')) for r in items]


def ask(gemma, schema, system, instruction, images, attempt):
    response = gemma({
        'model': 'gemma4:12b', 'stream': False, 'think': False, 'format': schema,
        'options': {'temperature': 0.3 if attempt > 1 else 0, 'seed': attempt, 'num_ctx': 16384, 'num_predict': 256},
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': instruction, 'images': images}]})
    return json.loads(response['message']['content'])


def judge(gemma, image, obj, attempt):
    note, examples = references(obj['cls'])
    images = views(image, obj) + examples
    result = ask(gemma, SCHEMA, SYSTEM, f"빨간 박스를 탐지 모델이 '{PPE[obj['cls']]}'(신뢰도 {obj['score']:.2f})로 판단했습니다. "
                 f"실제로 {PPE[obj['cls']]}입니까?" + note, images, attempt)
    if result.get('verdict') not in ('real', 'false', 'unsure'):
        raise ValueError('판정 값 오류')
    verdict = {'index': obj['index'], 'verdict': result['verdict'],
               'seen': str(result.get('seen', ''))[:100], 'reason': str(result.get('reason', ''))[:300]}
    if verdict['verdict'] == 'false':
        system = '이미지를 보고 질문에 yes·no·unsure로 답하고 근거를 한 문장으로 쓰세요. 확신이 없으면 unsure입니다.'
        if obj['cls'] == 'hard_hat':
            confirm = ask(gemma, CONFIRM_SCHEMA, system, CONFIRM_HAT + note, images, attempt)
            confirmed = confirm.get('answer') == 'no'
        else:
            confirm = ask(gemma, VEST_SCHEMA, system, CONFIRM_VEST + note, images, attempt)
            confirmed = all(confirm.get(k) == 'no' for k in ('reflective', 'fluorescent', 'layeredVest'))
        if not confirmed:
            verdict['verdict'] = 'unsure'
            detail = {k: confirm.get(k) for k in ('answer', 'reflective', 'fluorescent', 'layeredVest') if k in confirm}
            verdict['reason'] = (verdict['reason'] + f' / 재확인 엇갈림 {detail}: ' + str(confirm.get('reason', '')))[:300]
    return verdict


def check(job, gemma, OllamaDown):
    lease = {'leaseToken': job['leaseToken']}
    began = time.monotonic()
    try:
        image = Image.open(job['path']).convert('RGB')
        targets = [o for o in job['objects'] if o['cls'] in PPE]
        verdicts = [judge(gemma, image, o, job.get('attempt', 1)) for o in targets]
        api('/fp-checks/' + job['id'] + '/complete', {**lease, 'verdicts': verdicts, 'model': 'gemma4:12b', 'promptVersion': PROMPT,
                                                      'elapsedMs': int((time.monotonic() - began) * 1000)})
        review = any(v['verdict'] != 'real' for v in verdicts)
        print('오탐 확인 완료', job['id'], job['cameraId'], job['at'],
              ' '.join(f"{o['cls']}={v['verdict']}({v['seen']})" for o, v in zip(targets, verdicts)),
              '→ 관리자 확인 대기' if review else '', f'{time.monotonic() - began:.1f}s', flush=True)
    except OllamaDown as exc:
        print('오탐 확인: 분석 모델 연결 실패, 대기열로 되돌림', job['id'], str(exc), flush=True)
        try:
            api('/fp-checks/' + job['id'] + '/release', {**lease, 'error': '분석 모델 연결 실패: ' + str(exc)[:100]})
        except Exception:
            pass
    except Exception as exc:
        print('오탐 확인 실패', job['id'], type(exc).__name__, str(exc)[:200], flush=True)
        try:
            api('/fp-checks/' + job['id'] + '/fail', {**lease, 'error': ('오탐 확인 실패: ' + type(exc).__name__)[:500]})
        except Exception:
            pass
