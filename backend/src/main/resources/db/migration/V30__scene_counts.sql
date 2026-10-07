-- 구간 판정을 사람별에서 장면 집계로 바꾼다(프롬프트 segment-v3). 사람이 많은 구간(20~35명)에서 사람마다 판정·근거를 쓰면
-- 응답이 길어져 구간당 21~42초가 걸리고 길이 초과가 났다. 이제 Gemma는 구간 전체의 인원 집계와 미착용 의심자(최대 3명)만 쓴다.
-- 집계는 구간에 보이는 사람 전체 기준이다. workers_no_helmet은 조끼를 입은 작업자 중 안전모 미착용 수다.
-- V30 이전 구간은 집계가 NULL이며 검색은 기존 사람별 판정(observation_persons)으로 한다.
ALTER TABLE observations
  ADD COLUMN people int CHECK (people >= 0),
  ADD COLUMN helmet_present int CHECK (helmet_present >= 0),
  ADD COLUMN helmet_absent int CHECK (helmet_absent >= 0),
  ADD COLUMN helmet_unknown int CHECK (helmet_unknown >= 0),
  ADD COLUMN vest_present int CHECK (vest_present >= 0),
  ADD COLUMN vest_absent int CHECK (vest_absent >= 0),
  ADD COLUMN vest_unknown int CHECK (vest_unknown >= 0),
  ADD COLUMN workers_no_helmet int CHECK (workers_no_helmet >= 0);
