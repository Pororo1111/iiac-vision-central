package com.example.central;

import java.time.OffsetDateTime;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface ObservationMapper {
  // ---- 구간 분석 작업 ----
  record Claimed(String batchId,String deviceId,String cameraId,OffsetDateTime startedAt,OffsetDateTime endedAt,String leaseToken,int attempts) {}
  record YoloTrack(String trackId,int frames,String helmet,String vest) {}

  @Insert("""
      INSERT INTO observations(batch_id,device_id,camera_id,started_at,ended_at)
      VALUES(#{batchId}::uuid,#{deviceId},#{cameraId},#{startedAt},#{endedAt}) ON CONFLICT (batch_id) DO NOTHING""")
  void enqueue(String batchId,String deviceId,String cameraId,OffsetDateTime startedAt,OffsetDateTime endedAt);

  /** 임대가 끝난 작업은 3회까지 다시 대기열로 보내고 그 뒤에는 실패로 둔다. */
  @Update("""
      UPDATE observations SET status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'QUEUED' END,
        error='워커 응답 시간 초과',lease_token=NULL,lease_until=NULL,
        finished_at=CASE WHEN attempts>=3 THEN now() END
      WHERE status='RUNNING' AND lease_until<now()""")
  int expire();

  /** 구간 시작 시각을 포함한 원본 녹화가 있어야 가져간다. 없으면 녹화가 등록될 때까지 기다린다. */
  @Select(value="""
      UPDATE observations SET status='RUNNING',attempts=attempts+1,lease_token=#{token}::uuid,lease_until=now()+interval '120 seconds'
      WHERE batch_id=(SELECT o.batch_id FROM observations o WHERE o.status='QUEUED' AND EXISTS(
          SELECT 1 FROM recordings r WHERE r.device_id=o.device_id AND r.camera_id=o.camera_id
            AND r.started_at<=o.started_at AND r.ended_at>o.started_at)
        ORDER BY o.created_at,o.batch_id FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING batch_id::text,device_id,camera_id,started_at,ended_at,lease_token::text,attempts""",affectData=true)
  Claimed claim(String token);

  @Select("SELECT payload::text FROM detection_batches WHERE batch_id=#{id}::uuid") String payload(String id);
  @Select("SELECT track_id,frames,helmet,vest FROM detection_tracks WHERE batch_id=#{id}::uuid ORDER BY track_id")
  List<YoloTrack> yoloTracks(String id);

  @Update("""
      UPDATE observations SET lease_until=now()+interval '120 seconds'
      WHERE batch_id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid AND lease_until>now()""")
  int heartbeat(String id,String token);

  @Update("""
      UPDATE observations SET status=#{status},summary=#{summary},skip_reason=#{skipReason},error=NULL,model=#{model},
        prompt_version=#{promptVersion},frame_count=#{frameCount},elapsed_ms=#{elapsedMs},has_thumbnail=#{thumbnail},
        lease_token=NULL,lease_until=NULL,finished_at=now()
      WHERE batch_id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int complete(String id,String token,String status,String summary,String skipReason,String model,String promptVersion,
      Integer frameCount,Integer elapsedMs,boolean thumbnail);
  @Select("SELECT has_thumbnail FROM observations WHERE batch_id=#{id}::uuid") Boolean hasThumbnail(String id);

  @Update("""
      UPDATE observations SET status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'QUEUED' END,error=#{error},
        lease_token=NULL,lease_until=NULL,finished_at=CASE WHEN attempts>=3 THEN now() END
      WHERE batch_id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int fail(String id,String token,String error);

  /** 분석 모델(Ollama) 연결 장애처럼 작업 자체의 문제가 아니면 시도 횟수를 쓰지 않고 대기열로 되돌린다. */
  @Update("""
      UPDATE observations SET status='QUEUED',attempts=greatest(attempts-1,0),error=#{error},lease_token=NULL,lease_until=NULL
      WHERE batch_id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int release(String id,String token,String error);

  /** 실패한 구간을 처음부터 다시 분석하도록 되돌린다. 이전 판정은 다음 완료 때 교체된다. */
  @Update("""
      <script>UPDATE observations SET status='QUEUED',attempts=0,error=NULL,finished_at=NULL
      WHERE status='FAILED'<if test="deviceId != null"> AND device_id=#{deviceId}</if></script>""")
  int retryFailed(String deviceId);

  @Delete("DELETE FROM observation_persons WHERE batch_id=#{id}::uuid") void clearPersons(String id);
  @Insert("""
      INSERT INTO observation_persons(batch_id,track_id,helmet,vest,evidence,yolo_helmet,yolo_vest)
      SELECT #{id}::uuid,#{trackId},#{helmet},#{vest},#{evidence},t.helmet,t.vest
      FROM (SELECT 1) one LEFT JOIN detection_tracks t ON t.batch_id=#{id}::uuid AND t.track_id=#{trackId}""")
  void person(String id,String trackId,String helmet,String vest,String evidence);

  // ---- 검색 ----
  record Segment(String batchId,String deviceId,String cameraId,OffsetDateTime startedAt,OffsetDateTime endedAt,String summary,
      String persons,String recordingId,Double offsetSeconds,boolean hasThumbnail) {}

  @Select("""
      <script>
      SELECT o.batch_id::text,o.device_id,o.camera_id,o.started_at,o.ended_at,o.summary,
        (SELECT coalesce(json_agg(json_build_object('trackId',p.track_id,'helmet',p.helmet,'vest',p.vest,'evidence',p.evidence,
            'yoloHelmet',p.yolo_helmet,'yoloVest',p.yolo_vest) ORDER BY p.track_id),'[]'::json)
         FROM observation_persons p WHERE p.batch_id=o.batch_id)::text AS persons,
        r.id::text AS recording_id,extract(epoch FROM o.started_at-r.started_at)::float8 AS offset_seconds,o.has_thumbnail
      FROM observations o
      LEFT JOIN LATERAL (SELECT id,started_at FROM recordings r WHERE r.device_id=o.device_id AND r.camera_id=o.camera_id
          AND r.started_at &lt;= o.started_at AND r.ended_at &gt; o.started_at ORDER BY r.started_at DESC,r.id LIMIT 1) r ON true
      WHERE o.status='COMPLETED'
      <if test="to != null"> AND o.started_at &lt; #{to}</if>
      <if test="from != null"> AND o.ended_at &gt; #{from}</if>
      <if test="deviceId != null"> AND o.device_id=#{deviceId}</if>
      <if test="cameraId != null"> AND o.camera_id=#{cameraId}</if>
      <if test="helmet != null or vest != null">
        AND EXISTS(SELECT 1 FROM observation_persons p WHERE p.batch_id=o.batch_id
          <if test="helmet != null"> AND p.helmet=#{helmet}</if><if test="vest != null"> AND p.vest=#{vest}</if>)
      </if>
      <if test="pattern != null">
        AND (o.summary ILIKE #{pattern} ESCAPE '\\' OR EXISTS(SELECT 1 FROM observation_persons p
          WHERE p.batch_id=o.batch_id AND p.evidence ILIKE #{pattern} ESCAPE '\\'))
      </if>
      ORDER BY o.started_at DESC,o.batch_id DESC LIMIT 50
      </script>""")
  List<Segment> search(String deviceId,String cameraId,OffsetDateTime from,OffsetDateTime to,String helmet,String vest,String pattern);

  record Camera(String deviceId,String cameraId,OffsetDateTime firstAt,OffsetDateTime lastAt,int segments) {}
  @Select("""
      SELECT device_id,camera_id,min(started_at) AS first_at,max(ended_at) AS last_at,count(*)::int AS segments
      FROM observations WHERE status='COMPLETED' GROUP BY device_id,camera_id ORDER BY device_id,camera_id LIMIT 50""")
  List<Camera> cameras();

  // ---- 질문 ----
  record Question(String id,String conversationId,int seq,String message,String status,String filter,String reply,String error,
      Integer elapsedMs,OffsetDateTime createdAt,OffsetDateTime finishedAt,String leaseToken,String trace) {}
  String QUESTION="id::text,conversation_id::text,seq,message,status,filter::text,reply,error,elapsed_ms,created_at,finished_at,lease_token::text,trace::text";

  @Insert("INSERT INTO search_conversations(id,created_by) VALUES(#{id}::uuid,#{user})") void conversation(String id,String user);
  @Select("SELECT EXISTS(SELECT 1 FROM search_conversations WHERE id=#{id}::uuid)") boolean conversationExists(String id);
  @Select("SELECT id FROM search_conversations WHERE id=#{id}::uuid FOR UPDATE") String lockConversation(String id);
  @Select("SELECT EXISTS(SELECT 1 FROM search_questions WHERE conversation_id=#{id}::uuid AND status IN ('QUEUED','RUNNING'))")
  boolean pending(String id);
  @Insert("""
      INSERT INTO search_questions(id,conversation_id,seq,message)
      SELECT #{id}::uuid,#{conversation}::uuid,coalesce(max(seq),0)+1,#{message} FROM search_questions WHERE conversation_id=#{conversation}::uuid""")
  void ask(String id,String conversation,String message);
  @Select("SELECT "+QUESTION+" FROM search_questions WHERE id=#{id}::uuid") Question question(String id);

  record ConversationSummary(String id,String title,int questions,OffsetDateTime createdAt,OffsetDateTime updatedAt) {}
  /** 관리자 본인의 지난 대화. 제목은 첫 질문이다. 질문이 없는 대화(생성 직후 실패 등)는 제외한다. */
  @Select("""
      SELECT c.id::text,(SELECT q.message FROM search_questions q WHERE q.conversation_id=c.id ORDER BY q.seq LIMIT 1) AS title,
        (SELECT count(*)::int FROM search_questions q WHERE q.conversation_id=c.id) AS questions,c.created_at,
        (SELECT max(q.created_at) FROM search_questions q WHERE q.conversation_id=c.id) AS updated_at
      FROM search_conversations c WHERE c.created_by=#{user}
        AND EXISTS(SELECT 1 FROM search_questions q WHERE q.conversation_id=c.id)
      ORDER BY updated_at DESC,c.id DESC LIMIT #{limit}""")
  List<ConversationSummary> conversations(String user,int limit);
  @Select("SELECT created_by FROM search_conversations WHERE id=#{id}::uuid") String conversationOwner(String id);
  @Delete("DELETE FROM search_conversations WHERE id=#{id}::uuid") int deleteConversation(String id);
  @Select("SELECT "+QUESTION+" FROM search_questions WHERE conversation_id=#{id}::uuid ORDER BY seq") List<Question> conversationQuestions(String id);
  @Select("SELECT "+QUESTION+" FROM search_questions WHERE conversation_id=#{id}::uuid AND seq<#{seq} AND status='COMPLETED' ORDER BY seq DESC LIMIT 5")
  List<Question> history(String id,int seq);

  /** 질문은 대화 응답이라 다시 시도하지 않고 실패로 알린다. */
  @Update("""
      UPDATE search_questions SET status='FAILED',error='질문 해석 시간이 초과되었습니다. 다시 질문해 주세요.',lease_token=NULL,finished_at=now()
      WHERE status='RUNNING' AND lease_until<now()""")
  int expireQuestions();
  /** 워커가 멈춰 가져가지 않은 질문은 무한정 기다리지 않고 실패로 알린다. */
  @Update("""
      UPDATE search_questions SET status='FAILED',error='영상 분석 워커가 응답하지 않습니다. 잠시 후 다시 질문해 주세요.',finished_at=now()
      WHERE status='QUEUED' AND created_at<now()-make_interval(secs=>#{seconds})""")
  int expireWaitingQuestions(int seconds);
  @Select(value="""
      UPDATE search_questions SET status='RUNNING',lease_token=#{token}::uuid,lease_until=now()+interval '90 seconds'
      WHERE id=(SELECT id FROM search_questions WHERE status='QUEUED' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING\s"""+QUESTION,affectData=true)
  Question claimQuestion(String token);
  @Update("""
      UPDATE search_questions SET status='COMPLETED',filter=#{filter}::jsonb,reply=#{reply},elapsed_ms=#{elapsedMs},trace=#{trace}::jsonb,
        lease_token=NULL,finished_at=now()
      WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int answer(String id,String token,String filter,String reply,Integer elapsedMs,String trace);
  @Update("""
      UPDATE search_questions SET status='CANCELED',error='관리자가 생성을 중지했습니다.',lease_token=NULL,finished_at=now()
      WHERE id=#{id}::uuid AND status IN ('QUEUED','RUNNING')""")
  int cancel(String id);
  @Update("""
      UPDATE search_questions SET status='FAILED',error=#{error},lease_token=NULL,finished_at=now()
      WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid""")
  int questionFailed(String id,String token,String error);
}
