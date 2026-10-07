package com.example.central;

import java.time.OffsetDateTime;
import java.util.List;
import org.apache.ibatis.annotations.*;

/** 오탐 확인 대기열(V33)·관리자 검토(V34)와 누적 데이터셋 local:admin-fp 집계. */
@Mapper
public interface FpCheckMapper {
  @Insert("""
      INSERT INTO fp_checks(id,batch_id,captured_at,device_id,camera_id)
      VALUES(gen_random_uuid(),#{batchId}::uuid,#{at},#{deviceId},#{cameraId})
      ON CONFLICT (batch_id,captured_at) DO NOTHING""")
  void enqueue(String batchId,OffsetDateTime at,String deviceId,String cameraId);

  /** 임대가 끝난 작업은 3회까지 다시 대기열로 보내고 그 뒤에는 실패로 둔다. */
  @Update("""
      UPDATE fp_checks SET status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'QUEUED' END,
        error='워커 응답 시간 초과',lease_token=NULL,lease_until=NULL,finished_at=CASE WHEN attempts>=3 THEN now() END
      WHERE status='RUNNING' AND lease_until<now()""")
  int expire();

  record Claimed(String id,String batchId,OffsetDateTime capturedAt,String deviceId,String cameraId,String leaseToken,int attempts) {}
  /** deviceId는 검증 테스트가 자기 장치의 작업만 가져가게 할 때 쓴다. 워커는 비워 둔다. */
  @Select(value="""
      <script>UPDATE fp_checks SET status='RUNNING',attempts=attempts+1,lease_token=#{token}::uuid,lease_until=now()+interval '120 seconds'
      WHERE id=(SELECT id FROM fp_checks WHERE status='QUEUED'<if test="deviceId != null"> AND device_id=#{deviceId}</if>
        ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING id::text,batch_id::text,captured_at,device_id,camera_id,lease_token::text,attempts</script>""",affectData=true)
  Claimed claim(String token,String deviceId);

  @Select("SELECT payload::text FROM detection_batches WHERE batch_id=#{id}::uuid") String payload(String id);
  record Snapshot(int width,int height) {}
  @Select("SELECT width,height FROM detection_snapshots WHERE batch_id=#{id}::uuid AND captured_at=#{at}")
  Snapshot snapshot(String id,OffsetDateTime at);

  record Running(String batchId,OffsetDateTime capturedAt) {}
  @Select("SELECT batch_id::text,captured_at FROM fp_checks WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid FOR UPDATE")
  Running running(String id,String token);

  @Update("""
      UPDATE fp_checks SET status='DONE',error=NULL,model=#{model},prompt_version=#{promptVersion},verdicts=#{verdicts}::jsonb,
        false_count=#{falseCount},unsure_count=#{unsureCount},review_status=#{review},elapsed_ms=#{elapsedMs},
        lease_token=NULL,lease_until=NULL,finished_at=now()
      WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int complete(String id,String token,String model,String promptVersion,String verdicts,int falseCount,int unsureCount,
      String review,Integer elapsedMs);

  @Update("""
      UPDATE fp_checks SET status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'QUEUED' END,error=#{error},
        lease_token=NULL,lease_until=NULL,finished_at=CASE WHEN attempts>=3 THEN now() END
      WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int fail(String id,String token,String error);
  /** 분석 모델 연결 장애는 작업 문제가 아니므로 시도 횟수를 쓰지 않고 되돌린다. */
  @Update("""
      UPDATE fp_checks SET status='QUEUED',attempts=greatest(attempts-1,0),error=#{error},lease_token=NULL,lease_until=NULL
      WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int release(String id,String token,String error);

  // ---- 관리자 검토 ----
  record Review(String id,String batchId,OffsetDateTime capturedAt,String deviceId,String cameraId,String reviewStatus,String verdicts) {}
  @Select("""
      SELECT id::text,batch_id::text,captured_at,device_id,camera_id,review_status,verdicts::text
      FROM fp_checks WHERE id=#{id}::uuid FOR UPDATE""")
  Review lockReview(String id);
  @Update("""
      UPDATE fp_checks SET review_status=#{status},removed=#{removed}::jsonb,reviewed_by=#{user},reviewed_at=now(),
        dataset_name=#{datasetName},dataset_split=#{datasetSplit},dataset_boxes=#{datasetBoxes}
      WHERE id=#{id}::uuid AND review_status='PENDING'""")
  int review(String id,String status,String removed,String user,String datasetName,String datasetSplit,Integer datasetBoxes);
  @Select("SELECT count(*)::int FROM fp_checks WHERE review_status='APPROVED' AND dataset_split='val'") int approvedVal();

  // ---- 누적 데이터셋 ----
  record SplitCounts(int train,int val,int boxes) {}
  @Select("""
      SELECT count(*) FILTER (WHERE dataset_split='train')::int AS train,count(*) FILTER (WHERE dataset_split='val')::int AS val,
        coalesce(sum(dataset_boxes),0)::int AS boxes
      FROM fp_checks WHERE review_status='APPROVED' AND dataset_name IS NOT NULL""")
  SplitCounts datasetCounts();
  @Select("SELECT dataset_name FROM fp_checks WHERE review_status='APPROVED' AND dataset_name IS NOT NULL") List<String> datasetNames();
  @Select("SELECT id::text FROM external_datasets WHERE url=#{url} ORDER BY created_at LIMIT 1 FOR UPDATE") String externalId(String url);
  @Insert("""
      INSERT INTO external_datasets(id,url,workspace,project,version,status,name,yaml_path,classes,mapping,counts,created_at,finished_at)
      VALUES(gen_random_uuid(),#{url},'admin','fp',1,'READY',#{name},#{yaml},
        '["person","safety_vest","traffic_cone","hard_hat"]'::jsonb,'[0,1,2,3]'::jsonb,#{counts}::jsonb,now(),now())""")
  void insertExternal(String url,String name,String yaml,String counts);
  @Update("UPDATE external_datasets SET counts=#{counts}::jsonb,yaml_path=#{yaml},finished_at=now() WHERE id=#{id}::uuid")
  void updateExternal(String id,String yaml,String counts);

  // ---- 관리자 조회 ----
  record Item(String id,String batchId,String deviceId,String cameraId,OffsetDateTime capturedAt,String status,String reviewStatus,
      String verdicts,Integer falseCount,Integer unsureCount,String removed,String reviewedBy,OffsetDateTime reviewedAt,
      String datasetSplit,String error,Integer elapsedMs,OffsetDateTime finishedAt) {}
  String COLUMNS="""
      id::text,batch_id::text,device_id,camera_id,captured_at,status,review_status,verdicts::text,false_count,unsure_count,removed::text,
      reviewed_by,reviewed_at,dataset_split,error,elapsed_ms,finished_at""";
  /** 검토 목록. review가 PENDING이면 촬영 순(처리 순서), 아니면 최근 등록 순. 기간은 촬영 시각 기준 [from,to). */
  @Select("<script>SELECT "+COLUMNS+" FROM fp_checks WHERE true"
      +"<if test=\"review != null\"> AND review_status=#{review}</if>"
      +"<if test=\"deviceId != null\"> AND device_id=#{deviceId}</if>"
      +"<if test=\"cameraId != null\"> AND camera_id=#{cameraId}</if>"
      +"<if test=\"from != null\"> AND captured_at&gt;=#{from}</if>"
      +"<if test=\"to != null\"> AND captured_at&lt;#{to}</if>"
      +" ORDER BY <choose><when test=\"review == 'PENDING'\">captured_at,id</when><otherwise>created_at DESC,id</otherwise></choose>"
      +" LIMIT #{limit}</script>")
  List<Item> list(String review,String deviceId,String cameraId,OffsetDateTime from,OffsetDateTime to,int limit);
  @Select("SELECT "+COLUMNS+" FROM fp_checks WHERE id=#{id}::uuid") Item item(String id);
  record Summary(int queued,int running,int failed,int pending,int approved,int rejected,int notNeeded) {}
  @Select("""
      SELECT count(*) FILTER (WHERE status='QUEUED')::int AS queued,count(*) FILTER (WHERE status='RUNNING')::int AS running,
        count(*) FILTER (WHERE status='FAILED')::int AS failed,count(*) FILTER (WHERE review_status='PENDING')::int AS pending,
        count(*) FILTER (WHERE review_status='APPROVED')::int AS approved,count(*) FILTER (WHERE review_status='REJECTED')::int AS rejected,
        count(*) FILTER (WHERE review_status='NOT_NEEDED')::int AS not_needed
      FROM fp_checks""")
  Summary summary();
}
