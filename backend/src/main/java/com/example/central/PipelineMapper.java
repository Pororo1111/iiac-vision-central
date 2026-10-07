package com.example.central;

import com.example.central.PipelineModels.*;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface PipelineMapper {
  String EXT_HEAD="id::text,url,workspace,project,version,status,name,classes::text,mapping::text,counts::text,";
  String EXT_TAIL=",error,created_at,finished_at,phase,progress";
  String EXT_ADMIN=EXT_HEAD+"null::text AS lease_token"+EXT_TAIL;
  String EXT_WORKER=EXT_HEAD+"lease_token::text"+EXT_TAIL;
  String PIPE="id::text,name,source_kind,source::text,external::text,options::text,stage,run_id::text,sample_count,labeled_count,message,created_by,created_at,updated_at,name_status";

  @Insert("INSERT INTO external_datasets(id,url,workspace,project,version) VALUES(#{id}::uuid,#{url},#{workspace},#{project},#{version})")
  void insertExternal(String id,String url,String workspace,String project,Integer version);
  @Select("SELECT "+EXT_ADMIN+" FROM external_datasets WHERE workspace=#{workspace} AND project=#{project} AND version IS NOT DISTINCT FROM #{version} AND status<>'FAILED' ORDER BY created_at DESC LIMIT 1")
  External sameExternal(String workspace,String project,Integer version);
  @Select("SELECT "+EXT_ADMIN+" FROM external_datasets ORDER BY created_at DESC,id LIMIT 30") List<External> externals();
  @Select("SELECT "+EXT_ADMIN+" FROM external_datasets WHERE id=#{id}::uuid") External external(String id);
  @Select("""
      WITH RECURSIVE chain(id,base,depth) AS (
        SELECT id,base_model,0 FROM training_runs WHERE id=#{id}::uuid
        UNION ALL
        SELECT r.id,r.base_model,c.depth+1 FROM chain c JOIN training_runs r
          ON c.base LIKE 'release:%' AND r.id::text=substring(c.base FROM 9) WHERE c.depth<50)
      SELECT DISTINCT e->>'id' FROM chain c JOIN training_runs t ON t.id=c.id CROSS JOIN jsonb_array_elements(t.external) e""")
  List<String> lineageExternal(String id);
  @Select("SELECT yaml_path FROM external_datasets WHERE id=#{id}::uuid AND status='READY'") String externalPath(String id);
  @Select(value="""
      UPDATE external_datasets SET status='RUNNING',lease_token=#{token}::uuid,lease_until=now()+interval '120 seconds'
      WHERE id=(SELECT id FROM external_datasets WHERE status='QUEUED' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING
      """+EXT_WORKER,affectData=true)
  External claimExternal(String token);
  @Update("UPDATE external_datasets SET lease_until=now()+interval '120 seconds' WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid AND lease_until>now()")
  int heartbeatExternal(String id,String token);
  @Update("""
      UPDATE external_datasets SET lease_until=now()+interval '120 seconds',phase=COALESCE(#{phase},phase),progress=#{progress}
      WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid AND lease_until>now()""")
  int progressExternal(String id,String token,String phase,Integer progress);
  @Delete("DELETE FROM external_datasets WHERE id=#{id}::uuid AND status='FAILED'") int deleteFailedExternal(String id);
  @Update("UPDATE external_datasets SET status='FAILED',error='워커 응답 시간 초과. 다시 요청하세요.',finished_at=now() WHERE status='RUNNING' AND lease_until<now()")
  int expireExternal();
  @Update("""
      UPDATE external_datasets SET status='READY',name=#{name},version=#{version},yaml_path=#{yaml},classes=#{classes}::jsonb,
        mapping=#{mapping}::jsonb,counts=#{counts}::jsonb,error=null,finished_at=now() WHERE id=#{id}::uuid""")
  void readyExternal(String id,String name,int version,String yaml,String classes,String mapping,String counts);
  @Update("UPDATE external_datasets SET status='FAILED',error=#{error},finished_at=now() WHERE id=#{id}::uuid") void failExternal(String id,String error);

  @Insert("""
      INSERT INTO pipelines(id,name,source_kind,source,external,options,created_by,name_status)
      VALUES(#{id}::uuid,#{name},#{kind},#{source}::jsonb,#{external}::jsonb,#{options}::jsonb,#{user},'PENDING')""")
  void insert(String id,String name,String kind,String source,String external,String options,String user);
  @Select("SELECT "+PIPE+" FROM pipelines ORDER BY created_at DESC,id LIMIT 30") List<Pipeline> pipelines();
  @Select("SELECT "+PIPE+" FROM pipelines WHERE id=#{id}::uuid") Pipeline pipeline(String id);
  @Select("SELECT "+PIPE+" FROM pipelines WHERE id=#{id}::uuid FOR UPDATE SKIP LOCKED") Pipeline lock(String id);
  @Select("SELECT id::text FROM pipelines WHERE stage IN ('COLLECTING','LABELING','DATASET') ORDER BY created_at,id") List<String> active();
  @Update("""
      UPDATE pipelines SET stage=#{stage},run_id=#{run}::uuid,sample_count=#{samples},labeled_count=#{labeled},message=#{message},updated_at=now()
      WHERE id=#{id}::uuid""")
  void progress(String id,String stage,String run,int samples,int labeled,String message);
  @Update("UPDATE pipelines SET stage='CANCELLED',message='관리자가 취소했습니다.',updated_at=now() WHERE id=#{id}::uuid AND stage IN ('COLLECTING','LABELING','DATASET')")
  int cancel(String id);

  @Select("SELECT enabled,run_at,min_samples,epochs,base_policy,last_run_date,last_result,updated_at FROM auto_pipeline_settings WHERE id=1")
  AutoSettings autoSettings();
  @Select("SELECT enabled,run_at,min_samples,epochs,base_policy,last_run_date,last_result,updated_at FROM auto_pipeline_settings WHERE id=1 FOR UPDATE")
  AutoSettings lockAutoSettings();
  @Update("UPDATE auto_pipeline_settings SET enabled=#{enabled},run_at=#{runAt}::time,min_samples=#{minSamples},epochs=#{epochs},base_policy=#{basePolicy},updated_at=now() WHERE id=1")
  void updateAutoSettings(boolean enabled,String runAt,int minSamples,int epochs,String basePolicy);
  @Update("UPDATE auto_pipeline_settings SET last_run_date=#{date},last_result=#{result} WHERE id=1") void autoRan(java.time.LocalDate date,String result);
  // 삭제: 학습 작업 → 실행 → 파이프라인 순서로 지운다. 실행의 스냅샷이 사라지면 그 이미지는 다시 '학습에 안 쓴 이미지'가 된다.
  @Delete("DELETE FROM training_tasks WHERE run_id=#{run}::uuid") void deleteTasks(String run);
  @Delete("DELETE FROM training_runs WHERE id=#{run}::uuid") void deleteRun(String run);
  @Delete("DELETE FROM pipelines WHERE id=#{id}::uuid") int delete(String id);
  @Select("SELECT count(*) FROM training_tasks WHERE run_id=#{run}::uuid AND status IN ('QUEUED','RUNNING')") int activeTasks(String run);
  @Select("SELECT id::text FROM pipelines WHERE source_kind='AUTO' ORDER BY created_at DESC,id LIMIT 1") String latestAuto();
  @Select("SELECT count(*) FROM labeling_jobs WHERE status IN ('QUEUED','RUNNING')") long labelingQueued();

  /** 이름이 없는 파이프라인 하나를 5분간 임대한다. 학습 실행을 만들기 전에 이름을 먼저 정한다. */
  @Select(value="""
      UPDATE pipelines SET name_status='GENERATING',name_lease_until=now()+interval '5 minutes'
      WHERE id=(SELECT p.id FROM pipelines p
        WHERE p.name_status='PENDING' OR (p.name_status='GENERATING' AND p.name_lease_until<now())
        ORDER BY p.created_at,p.id FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING id::text""",affectData=true)
  String claimNaming();
  /** 학습 직전 이름 대기 기한(3분)을 정한다. 워커가 임대하면 임대 기한으로 바뀐다. */
  @Update("UPDATE pipelines SET name_lease_until=now()+interval '3 minutes' WHERE id=#{id}::uuid AND name_status='PENDING' AND name_lease_until IS NULL")
  void nameDeadline(String id);
  /** 대기·임대 기한이 지나도록 이름이 없으면 규칙 이름을 쓴다. */
  @Update("UPDATE pipelines SET name=#{name},name_status='DONE',name_lease_until=null,updated_at=now() WHERE id=#{id}::uuid AND name_status<>'DONE' AND name_lease_until<now()")
  int fallbackNamed(String id,String name);
  @Select("SELECT count(*) FROM pipelines WHERE name=#{name} AND id<>#{id}::uuid") int sameName(String id,String name);
  @Update("UPDATE pipelines SET name=#{name},name_status='DONE',name_lease_until=null,updated_at=now() WHERE id=#{id}::uuid AND name_status='GENERATING'")
  int named(String id,String name);
  @Update("UPDATE training_runs SET name=#{name} WHERE id=#{run}::uuid") void renameRun(String run,String name);

  /** 파이프라인 대상 이미지. busy는 자동 라벨링 작업이 대기·실행 중인지 여부다. */
  String MEMBER="SELECT s.id::text,s.status,EXISTS(SELECT 1 FROM labeling_jobs j WHERE j.sample_id=s.id AND j.status IN ('QUEUED','RUNNING')) AS busy FROM training_samples s ";
  @Select("""
      <script>""" + MEMBER + """
      WHERE s.device_id IN <foreach item='d' collection='devices' open='(' separator=',' close=')'>#{d}</foreach>
      <if test='!all'>AND s.created_at &gt;= #{since}</if>
      ORDER BY s.created_at DESC,s.id LIMIT 200</script>""")
  List<Member> deviceMembers(List<String> devices,boolean all,java.time.OffsetDateTime since);
  @Select("""
      <script>""" + MEMBER + """
      WHERE false
      <if test='samples.size() > 0'>OR s.id IN <foreach item='i' collection='samples' open='(' separator=',' close=')'>#{i}::uuid</foreach></if>
      <if test='imports.size() > 0'>OR s.import_id IN <foreach item='i' collection='imports' open='(' separator=',' close=')'>#{i}::uuid</foreach></if>
      ORDER BY s.created_at DESC,s.id LIMIT 200</script>""")
  List<Member> uploadMembers(List<String> samples,List<String> imports);
  @Select("""
      <script>SELECT count(*) FROM media_imports WHERE status IN ('QUEUED','RUNNING') AND id IN
      <foreach item='i' collection='imports' open='(' separator=',' close=')'>#{i}::uuid</foreach></script>""")
  int pendingImports(List<String> imports);
  @Select("<script>SELECT count(*) FROM media_imports WHERE id IN <foreach item='i' collection='imports' open='(' separator=',' close=')'>#{i}::uuid</foreach></script>")
  int importCount(List<String> imports);
  @Select("<script>SELECT count(*) FROM training_samples WHERE id IN <foreach item='i' collection='samples' open='(' separator=',' close=')'>#{i}::uuid</foreach></script>")
  int sampleCount(List<String> samples);
}
