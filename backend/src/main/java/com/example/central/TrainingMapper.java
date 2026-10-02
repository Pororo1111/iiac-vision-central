package com.example.central;

import com.example.central.TrainingModels.*;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface TrainingMapper {
  String RUN="id::text,name,state,snapshot::text,summary::text,automation::text,created_at,updated_at,base_model,external::text";
  String TASK="id::text,run_id::text,action,status,parameters::text,lease_token::text,error";
  @Select("SELECT "+RUN+" FROM training_runs ORDER BY created_at DESC,id LIMIT 40") List<Run> runs();
  @Select("SELECT "+RUN+" FROM training_runs WHERE id=#{id}::uuid") Run run(String id);
  @Select("SELECT "+RUN+" FROM training_runs WHERE id=#{id}::uuid FOR UPDATE") Run lock(String id);
  @Insert("INSERT INTO training_runs(id,name,state,snapshot,automation,base_model,external) VALUES(#{id}::uuid,#{name},'PREPARING',#{snapshot}::jsonb,#{automation}::jsonb,#{baseModel},#{external}::jsonb)")
  void insert(String id,String name,String snapshot,String automation,String baseModel,String external);
  @Select("SELECT r.id::text,r.name,m.created_at,m.size_bytes,(r.summary->'evaluation'->'models'->'candidate'->>'mAP50_95')::float8 AS map FROM model_releases m JOIN training_runs r ON r.id=m.id ORDER BY m.created_at DESC,m.id LIMIT 20") List<Release> releases();
  @Select("SELECT count(*) FROM model_releases WHERE id=#{id}::uuid") int released(String id);
  @Update("UPDATE training_runs SET summary=summary || #{summary}::jsonb,updated_at=now() WHERE id=#{id}::uuid") void note(String id,String summary);
  @Select("SELECT count(*) FROM training_tasks WHERE status IN ('QUEUED','RUNNING')") int activeTasks();
  @Select("SELECT "+TASK+" FROM training_tasks WHERE run_id=#{run}::uuid AND status IN ('QUEUED','RUNNING') FOR UPDATE") Task activeTask(String run);
  /** 이전 실행 스냅샷에 들어간 적 없고 Gemma 태그가 하나 이상인 자동 라벨링 완료 이미지. 빈 장면은 선별을 통과할 수 없어 제외한다. */
  @Select("""
      SELECT s.id::text FROM training_samples s
      WHERE s.status='LABELED' AND jsonb_array_length(coalesce(s.proposal->'result'->'labels','[]'::jsonb))>0
        AND NOT EXISTS (SELECT 1 FROM training_runs r, jsonb_array_elements(r.snapshot) e WHERE e->>'id'=s.id::text)
      ORDER BY s.created_at DESC,s.id LIMIT #{limit}""") List<String> unusedLabeled(int limit);
  /** 자동 파이프라인 대상: 여러 IoT가 보낸, 아직 학습에 쓰지 않은 태그 있는 라벨링 완료 이미지. */
  String UNUSED_IOT="""
      FROM training_samples s
      WHERE s.source='IOT' AND s.status='LABELED' AND jsonb_array_length(coalesce(s.proposal->'result'->'labels','[]'::jsonb))>0
        AND NOT EXISTS (SELECT 1 FROM training_runs r, jsonb_array_elements(r.snapshot) e WHERE e->>'id'=s.id::text)""";
  @Select("SELECT s.id::text "+UNUSED_IOT+" ORDER BY s.created_at DESC,s.id LIMIT #{limit}") List<String> unusedIot(int limit);
  @Select("SELECT count(*) "+UNUSED_IOT) int unusedIotCount();
  @Select("SELECT id::text FROM model_releases WHERE kind='RUN' ORDER BY created_at DESC,id LIMIT 1") String latestRelease();
  @Insert("INSERT INTO training_tasks(id,run_id,action,status,parameters) VALUES(#{id}::uuid,#{run}::uuid,#{action},'QUEUED',#{parameters}::jsonb)") void enqueue(String id,String run,String action,String parameters);
  @Update("UPDATE training_runs SET state=#{state},summary=summary || #{summary}::jsonb,updated_at=now() WHERE id=#{id}::uuid") void state(String id,String state,String summary);
  @Select("SELECT "+TASK+" FROM training_tasks WHERE id=#{id}::uuid FOR UPDATE") Task task(String id);
  @Select("SELECT "+TASK+" FROM training_tasks WHERE id=#{id}::uuid") Task taskView(String id);
  @Select("SELECT "+TASK+" FROM training_tasks WHERE run_id=#{run}::uuid ORDER BY created_at DESC,id LIMIT 10") List<Task> tasks(String run);
  @Select(value="UPDATE training_tasks SET status='RUNNING',lease_token=#{token}::uuid,lease_until=now()+interval '120 seconds' WHERE id=(SELECT id FROM training_tasks WHERE status='QUEUED' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING "+TASK,affectData=true) Task claim(String token);
  @Update("UPDATE training_tasks SET lease_until=now()+interval '120 seconds' WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid AND lease_until>now()") int heartbeat(String id,String token);
  @Update("UPDATE training_tasks SET status=#{status},error=#{error} WHERE id=#{id}::uuid") void finish(String id,String status,String error);
  @Select("SELECT "+TASK+" FROM training_tasks WHERE status='RUNNING' AND lease_until<now() FOR UPDATE") List<Task> expired();
}
