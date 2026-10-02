"""영상 분석 워커의 응답 검증·이름 가림 규칙 단위 테스트(서버·Ollama 불필요). `.venv/bin/python tests/observer_checks.py`."""
import json
from pathlib import Path
import sys
import unittest
from urllib import error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ai-worker'))
import observer  # noqa: E402
from observer import InvalidOutput, OllamaDown, mask_names, name_candidates, parse_segment  # noqa: E402


def reply(content, done='stop'):
    return {'message': {'content': content if isinstance(content, str) else json.dumps(content)}, 'done_reason': done}


class Names(unittest.TestCase):
    def test_masks_names(self):
        names = name_candidates(['권도현 대리', '이영섭 PD', '안전제일', '모니터 앞 안전모'])
        self.assertEqual(names, {'권도현', '이영섭'})
        cases = {
            '한 명의 인물(권도현/이영섭)이 모니터 앞에 앉아 있습니다.': '한 명의 인물(○○○/○○○)이 모니터 앞에 앉아 있습니다.',
            '권도현이 안전모를 쓰지 않았습니다.': '○○○이 안전모를 쓰지 않았습니다.',
            '김철수 씨가 장비를 옮깁니다.': '○○○ 씨가 장비를 옮깁니다.',
            '두 사람(이영섭, 박민수)이 대화합니다.': '두 사람(○○○, ○○○)이 대화합니다.',
            '최민호 팀장이 설명합니다.': '○○○ 팀장이 설명합니다.',
        }
        for text, expected in cases.items():
            self.assertEqual(mask_names(text, names)[0], expected)

    def test_keeps_ordinary_words(self):
        for text in ['안전모, 안전화, 조끼를 착용했습니다.', '장갑/안전모/조끼', '(안전모)를 손에 들고 있습니다.',
                     '조끼를 입은 사람(오른쪽)', '왼쪽 인물은 고개를 숙이고 있으며, 오른쪽 인물은 서 있습니다.',
                     '작업자가 모니터를 봅니다. 이후 정면에 섭니다.']:
            self.assertEqual(mask_names(text, set()), (text, 0))


class Output(unittest.TestCase):
    refs = ['s1-t1']

    def test_valid(self):
        summary, persons, screen = parse_segment(reply({'screenText': ['자막'], 'summary': ' 요약 ', 'persons': [
            {'ref': 's1-t1', 'helmet': 'present', 'vest': 'unknown', 'evidence': '근거'},
            {'ref': 's1-t1', 'helmet': 'absent', 'vest': 'absent', 'evidence': '중복은 처음 것만'}]}), self.refs)
        self.assertEqual((summary, screen, len(persons)), ('요약', ['자막'], 1))
        self.assertEqual(persons[0]['helmet'], 'present')

    def test_invalid(self):
        person = {'ref': 's1-t1', 'helmet': 'present', 'vest': 'present', 'evidence': ''}
        cases = {
            '응답 길이 초과': reply({'summary': 's', 'persons': []}, done='length'),
            'JSON 아님': reply('{"summary": "잘린'),
            '객체 아님': reply([]),
            '요약 없음': reply({'summary': ' ', 'persons': []}),
            '사람 목록 형식 오류': reply({'summary': 's', 'persons': {}}),
            '화면 글자 형식 오류': reply({'summary': 's', 'persons': [], 'screenText': 'x'}),
            '알 수 없는 사람 참조': reply({'summary': 's', 'persons': [{**person, 'ref': 's9-t9'}]}),
            '허용되지 않은 판정값': reply({'summary': 's', 'persons': [{**person, 'helmet': 'yes'}]}),
        }
        for message, response in cases.items():
            with self.assertRaises(InvalidOutput, msg=message) as caught:
                parse_segment(response, self.refs)
            self.assertEqual(str(caught.exception), message)


class Connection(unittest.TestCase):
    def call(self, exc):
        original = observer.ollama
        def broken(*_):
            raise exc
        observer.ollama = broken
        try:
            observer.gemma({})
        finally:
            observer.ollama = original

    def test_refused_is_ollama_down(self):
        for exc in (error.URLError(ConnectionRefusedError(61, 'refused')), ConnectionResetError()):
            with self.assertRaises(OllamaDown):
                self.call(exc)

    def test_model_errors_and_timeouts_are_job_failures(self):
        # 모델 오류·시간 초과는 시도 횟수를 써서 고장 난 모델이 같은 작업을 끝없이 되돌리지 않게 한다.
        for exc in (error.HTTPError('u', 500, 'x', {}, None), error.URLError(TimeoutError()), TimeoutError()):
            with self.assertRaises(Exception) as caught:
                self.call(exc)
            self.assertNotIsInstance(caught.exception, OllamaDown)


if __name__ == '__main__':
    unittest.main(verbosity=1)
