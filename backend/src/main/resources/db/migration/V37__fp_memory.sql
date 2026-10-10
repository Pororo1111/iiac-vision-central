-- 오탐 판단 기억(RAG, 2026-10-09). 사람이 정탐·오탐을 판단한 보호구 박스를 CLIP 이미지 임베딩으로 저장하고,
-- 새 오탐 확인 때 같은 클래스의 가장 비슷한 사례를 찾아 판정 근거로 쓴다.
-- 출처: CLAUDE(10/07·10/08 웹캠 사진을 Claude가 직접 판별한 기본 사례, scripts/seed-fp-memory.py),
--       ADMIN(관리자가 오탐 확인 창에서 승인한 박스. 승인 즉시 행이 생기고 워커가 임베딩을 채운다).
-- 임베딩 계산은 호스트 워커(ai-worker/fp_memory.py)가 하고, 중앙은 저장·유사도 검색(pgvector 코사인 거리)만 한다.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE fp_memory (
  id uuid PRIMARY KEY,
  source varchar(8) NOT NULL CHECK (source IN ('CLAUDE', 'ADMIN')),
  cls varchar(32) NOT NULL,
  label varchar(8) NOT NULL CHECK (label IN ('real', 'false')),
  -- ADMIN: 판단한 오탐 확인 행과 박스 index. 장치 삭제 등으로 원본이 지워지면 함께 지운다.
  fp_check_id uuid REFERENCES fp_checks(id) ON DELETE CASCADE,
  box_index int,
  -- 원본 사진 기준 0~1 정규화 [x1,y1,x2,y2]
  box jsonb NOT NULL,
  -- CLAUDE: 원본 식별자(작업 파일 키). 다시 넣어도 중복되지 않게 한다.
  origin varchar(200) UNIQUE,
  note varchar(200),
  created_by varchar(64),
  embedding vector(512),
  embed_model varchar(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  embedded_at timestamptz,
  UNIQUE (fp_check_id, box_index),
  CHECK ((source = 'ADMIN') = (fp_check_id IS NOT NULL))
);
CREATE INDEX fp_memory_embedding ON fp_memory USING hnsw (embedding vector_cosine_ops);
CREATE INDEX fp_memory_pending ON fp_memory(created_at, id) WHERE embedding IS NULL;
