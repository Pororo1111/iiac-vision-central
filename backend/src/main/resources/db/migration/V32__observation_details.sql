-- Gemma가 원본 이미지를 보고 쓴 검색용 발견 사항(한 줄에 하나, 최대 6개, segment-v6).
-- 관리자가 대화로 "사다리 위 작업자", "누워 있는 사람"처럼 물으면 요약과 함께 이 글을 부분 일치로 찾는다.
-- v6부터 사람별 판정(observation_persons)은 만들지 않는다. Gemma에 박스·라벨 없는 원본만 주기 때문이다.
ALTER TABLE observations ADD COLUMN details text;
