package com.example.central;

import com.example.central.LabelModels.*;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface LabelMapper {
  String SAMPLE = "id::text,sha256,title,mime,width,height,device_id,camera_id,captured_at,status,revision,proposal::text,annotation::text,created_at,source,import_id::text";
  String JOB = "id::text,sample_id::text,sample_revision,instruction,model,status,lease_token::text,lease_until,error,created_at,finished_at,result::text,kind";
  String FILTER = "<where><if test='device != null'>device_id=#{device}</if><if test='status != null'> AND status=#{status}</if></where>";
  @Select("<script>SELECT " + SAMPLE + " FROM training_samples " + FILTER + " ORDER BY created_at DESC,id LIMIT 40 OFFSET #{offset}</script>")
  List<Sample> samples(int offset,String device,String status);
  @Select("<script>SELECT count(*) FROM training_samples " + FILTER + "</script>")
  long count(String device,String status);
  @Select("""
      SELECT count(*) AS total,count(*) FILTER (WHERE status='NEW') AS unprocessed,
      count(*) FILTER (WHERE status='REVIEW') AS pending_review,count(*) FILTER (WHERE status='APPROVED') AS approved,count(*) FILTER (WHERE status='LABELED') AS labeled,
      count(*) FILTER (WHERE status='EXCLUDED') AS excluded,
      (SELECT count(*) FROM labeling_jobs WHERE status='QUEUED') AS queued,
      (SELECT count(*) FROM labeling_jobs WHERE status='RUNNING') AS running,max(created_at) AS last_received_at,
      (SELECT count(*) FROM labeling_jobs WHERE status='QUEUED' AND kind='AUTO_LABEL') AS auto_queued,
      (SELECT count(*) FROM labeling_jobs WHERE status='RUNNING' AND kind='AUTO_LABEL') AS auto_running
      FROM training_samples
      """) Stats stats();
  @Select("SELECT " + SAMPLE + " FROM training_samples WHERE id=#{id}::uuid") Sample sample(String id);
  @Select("SELECT " + SAMPLE + " FROM training_samples WHERE device_id=#{device} AND camera_id=#{camera} AND captured_at=#{captured} AND sha256=#{hash}")
  Sample duplicate(String device,String camera,java.time.OffsetDateTime captured,String hash);
  @Select("SELECT " + SAMPLE + " FROM training_samples WHERE source='UPLOAD' AND sha256=#{hash} ORDER BY created_at,id LIMIT 1")
  Sample uploaded(String hash);
  @Select("SELECT " + SAMPLE + " FROM training_samples WHERE import_id=#{importId}::uuid AND sha256=#{hash}")
  Sample imported(String importId,String hash);
  @Select("SELECT " + SAMPLE + " FROM training_samples WHERE id=#{id}::uuid FOR UPDATE") Sample lock(String id);
  @Insert("""
      INSERT INTO training_samples(id,sha256,title,mime,width,height,device_id,camera_id,captured_at,is_reference,source,import_id)
      VALUES(#{id}::uuid,#{sha256},#{title},#{mime},#{width},#{height},#{deviceId},#{cameraId},#{capturedAt},false,#{source},#{importId}::uuid)
      """) void insert(Sample sample);
  @Select("SELECT count(*) FROM labeling_jobs WHERE sample_id=#{id}::uuid AND status IN ('QUEUED','RUNNING')") int live(String id);
  @Update("UPDATE training_samples SET status=#{status},annotation=#{annotation}::jsonb,revision=revision+1 WHERE id=#{id}::uuid")
  void review(String id, String status, String annotation);
  @Insert("INSERT INTO annotation_reviews(sample_id,revision,reviewer,status,annotation) VALUES(#{id}::uuid,#{revision},#{reviewer},#{status},#{annotation}::jsonb)")
  void audit(String id, int revision, String reviewer, String status, String annotation);
  @Insert("INSERT INTO labeling_jobs(id,sample_id,sample_revision,reference_ids,instruction,kind) VALUES(#{id}::uuid,#{sampleId}::uuid,#{revision},'[]'::jsonb,#{instruction},#{kind})")
  void enqueue(String id, String sampleId, int revision, String instruction, String kind);
  @Select("<script>SELECT " + JOB + " FROM labeling_jobs <where><if test='sampleId != null'>sample_id=#{sampleId}::uuid</if></where> ORDER BY created_at DESC,id LIMIT 40 OFFSET #{offset}</script>") List<Job> jobs(int offset,String sampleId);
  @Select("SELECT " + JOB + " FROM labeling_jobs WHERE id=#{id}::uuid FOR UPDATE") Job job(String id);
  @Select("SELECT " + JOB + " FROM labeling_jobs WHERE id=#{id}::uuid") Job peek(String id);
  @Update("UPDATE labeling_jobs SET status='FAILED',error='워커 응답 시간 초과. 다시 요청하세요.',finished_at=now() WHERE status='RUNNING' AND lease_until < now()") int expire();
  @Select(value="""
      UPDATE labeling_jobs SET status='RUNNING',lease_token=#{token}::uuid,lease_until=now()+interval '120 seconds'
      WHERE id=(SELECT id FROM labeling_jobs WHERE status='QUEUED' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING
      """ + JOB, affectData=true)
  Job claim(String token);
  @Update("UPDATE labeling_jobs SET lease_until=now()+interval '120 seconds' WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid AND lease_until>now()")
  int heartbeat(String id,String token);
  @Update("UPDATE labeling_jobs SET status=#{status},error=#{error},finished_at=now() WHERE id=#{id}::uuid")
  void finish(String id,String status,String error);
  @Update("UPDATE labeling_jobs SET result=#{result}::jsonb WHERE id=#{id}::uuid")
  void result(String id,String result);
  @Insert("INSERT INTO discarded_samples(id,source,device_id,reason) VALUES(#{id}::uuid,#{source},#{device},#{reason}) ON CONFLICT(id) DO NOTHING")
  void discarded(String id,String source,String device,String reason);
  @Insert("INSERT INTO pending_media_deletions(sample_id) VALUES(#{id}::uuid) ON CONFLICT DO NOTHING") void queueDeletion(String id);
  @Delete("DELETE FROM pending_media_deletions WHERE sample_id=#{id}::uuid") void deletionDone(String id);
  @Delete("DELETE FROM annotation_reviews WHERE sample_id=#{id}::uuid") void deleteReviews(String id);
  @Delete("DELETE FROM labeling_jobs WHERE sample_id=#{id}::uuid") void deleteJobs(String id);
  @Delete("DELETE FROM training_samples WHERE id=#{id}::uuid") void deleteSample(String id);
  @Select("SELECT count(*) FROM discarded_samples WHERE discarded_at >= #{since}") long discardedSince(java.time.OffsetDateTime since);
  @Update("UPDATE training_samples SET proposal=#{proposal}::jsonb,status='LABELED',revision=revision+1 WHERE id=#{id}::uuid AND revision=#{revision}")
  int proposal(String id,int revision,String proposal);
}
