"""로컬 개발 DB에 독립 테스트 장치를 만들고 종료 시 데이터·이미지를 제거한다."""
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from dev import environment, DATA, PG


def sql(statement):
    return subprocess.check_output([str(PG / 'psql'), '-v', 'ON_ERROR_STOP=1', '-Atc', statement], env=environment(), text=True)


class DeviceFixture:
    def __init__(self):
        self.id = 'verify-' + uuid.uuid4().hex[:16]
        self.token = secrets.token_hex(32)
        digest = hashlib.sha256(self.token.encode()).hexdigest()
        sql(f"INSERT INTO devices(device_id,name,token_hash) VALUES('{self.id}','일시 검증 장치','{digest}')")

    def cleanup(self):
        samples = sql(f"SELECT id::text FROM training_samples WHERE device_id='{self.id}'").splitlines()
        sql(f"""BEGIN;
          DELETE FROM annotation_reviews WHERE sample_id IN (SELECT id FROM training_samples WHERE device_id='{self.id}');
          DELETE FROM labeling_jobs WHERE sample_id IN (SELECT id FROM training_samples WHERE device_id='{self.id}');
          DELETE FROM training_samples WHERE device_id='{self.id}';
          DELETE FROM events WHERE device_id='{self.id}';
          DELETE FROM devices WHERE device_id='{self.id}'; COMMIT;""")
        for sample in samples:
            (DATA / 'media' / (str(uuid.UUID(sample)) + '.jpg')).unlink(missing_ok=True)
