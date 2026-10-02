"""현장 기본 모델(BASE) 후보 등록을 확인한다. 실제 ppe4 기본 모델을 등록하며 이미 등록돼 있으면 같은 후보를 돌려받는다."""
import json
from training_workflow import cli, BASE, post, expect


def main():
    admin = cli.admin_session(BASE)
    expect(403, lambda: post(admin, '/training/models/base', {}, False))
    first = post(admin, '/training/models/base', {})
    again = post(admin, '/training/models/base', {})
    assert first == again, (first, again)
    with admin.open(BASE + '/api/training/models') as response:
        catalog = {m['id']: m for m in json.load(response)}
    base = catalog[first['id']]
    assert base['source'] == 'BASE' and base['sha256'] == first['sha256'], base
    assert all(m['source'] in ('BASE', 'MANUAL', 'AUTO') for m in catalog.values())
    print(f"기본 모델 후보: CSRF·중복 등록 방지·분류 검사 통과 ({base['name']}, mAP50-95 {base['candidateMap']})")


if __name__ == '__main__':
    main()
