package com.example.central;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.apache.ibatis.annotations.*;

@Mapper
public interface DetectionMapper {
  /** box는 0~1 정규화 [x1,y1,x2,y2]. trackId는 사람(person)에만 의미가 있다. */
  record DetectedObject(
      @NotBlank @Pattern(regexp="[a-z_]{1,32}") String cls,
      @NotNull @DecimalMin("0") @DecimalMax("1") Double score,
      @NotNull @Size(min=4,max=4) List<@NotNull @DecimalMin("0") @DecimalMax("1") Double> box,
      @Pattern(regexp="[A-Za-z0-9_-]{1,32}") String trackId) {}
  record Frame(@NotNull OffsetDateTime at, @NotNull @Size(max=100) List<@NotNull @Valid DetectedObject> objects) {}
  record DetectionBatch(
      @NotNull UUID batchId,
      @NotBlank @Pattern(regexp="[A-Za-z0-9_-]{1,64}") String deviceId,
      @NotBlank @Pattern(regexp="[A-Za-z0-9_-]{1,64}") String cameraId,
      @NotNull UUID sessionId,
      @NotNull OffsetDateTime startedAt,
      @NotNull OffsetDateTime endedAt,
      @NotBlank @Size(max=128) String model,
      @NotBlank @Pattern(regexp="[A-Za-z0-9_.-]{1,32}") String tracker,
      @NotNull @Size(max=100) List<@NotNull OffsetDateTime> cuts,
      @NotNull @Size(max=600) List<@NotNull @Valid Frame> frames) {}

  record Track(String trackId,OffsetDateTime firstSeen,OffsetDateTime lastSeen,int frames,double maxScore,
      int helmetFrames,int vestFrames,String helmet,String vest) {}
  record Summary(List<Track> tracks,int untrackedPersons,int unassignedHardHats,int unassignedVests) {}
  record TrackView(String batchId,String sessionId,String trackId,OffsetDateTime firstSeen,OffsetDateTime lastSeen,int frames,
      double maxScore,int helmetFrames,int vestFrames,String helmet,String vest,String ruleVersion) {}

  @Insert("""
      INSERT INTO detection_batches(batch_id,device_id,camera_id,session_id,started_at,ended_at,model_name,tracker,rule_version,
        frame_count,cut_count,untracked_persons,unassigned_hard_hats,unassigned_vests,payload,payload_sha256,source)
      VALUES(#{b.batchId},#{b.deviceId},#{b.cameraId},#{b.sessionId},#{b.startedAt},#{b.endedAt},#{b.model},#{b.tracker},#{rule},
        #{frameCount},#{cutCount},#{s.untrackedPersons},#{s.unassignedHardHats},#{s.unassignedVests},#{payload}::jsonb,#{sha},#{source})
      ON CONFLICT (batch_id) DO NOTHING""")
  int insertBatch(DetectionBatch b,String rule,int frameCount,int cutCount,Summary s,String payload,String sha,String source);

  @Insert("""
      INSERT INTO detection_tracks(batch_id,track_id,device_id,camera_id,session_id,first_seen,last_seen,frames,max_score,
        helmet_frames,vest_frames,helmet,vest)
      VALUES(#{b.batchId},#{t.trackId},#{b.deviceId},#{b.cameraId},#{b.sessionId},#{t.firstSeen},#{t.lastSeen},#{t.frames},
        #{t.maxScore},#{t.helmetFrames},#{t.vestFrames},#{t.helmet},#{t.vest})""")
  void insertTrack(DetectionBatch b,Track t);

  record Existing(String deviceId,String payloadSha256) {}
  @Select("SELECT device_id,payload_sha256 FROM detection_batches WHERE batch_id=#{id}::uuid") Existing existing(String id);

  // ---- 원본 프레임(오버레이 없는 스냅샷) ----
  record LockedBatch(String deviceId,String cameraId,String payload) {}
  @Select("SELECT device_id,camera_id,payload::text FROM detection_batches WHERE batch_id=#{id}::uuid FOR UPDATE")
  LockedBatch lockBatch(String id);
  @Select("SELECT sha256 FROM detection_snapshots WHERE batch_id=#{id}::uuid AND captured_at=#{at}")
  String snapshotSha(String id,OffsetDateTime at);
  @Select("SELECT count(*)::int FROM detection_snapshots WHERE batch_id=#{id}::uuid") int snapshotCount(String id);
  @Insert("""
      INSERT INTO detection_snapshots(batch_id,captured_at,sha256,size_bytes,width,height)
      VALUES(#{id}::uuid,#{at},#{sha},#{size},#{width},#{height})""")
  void insertSnapshot(String id,OffsetDateTime at,String sha,int size,int width,int height);
  @Select("SELECT EXISTS(SELECT 1 FROM detection_batches WHERE batch_id=#{id}::uuid)") boolean batchExists(String id);

  /** 최근 24시간 안에 장치가 직접 보낸 배치가 있으면 색인 워커는 그 카메라를 다시 탐지하지 않는다. */
  @Select("""
      SELECT EXISTS(SELECT 1 FROM detection_batches WHERE device_id=#{deviceId} AND camera_id=#{cameraId}
        AND source='IOT' AND received_at>now()-interval '24 hours')""")
  boolean iotActive(String deviceId,String cameraId);

  /** [from,to)와 겹치는 추적 요약. 순서는 첫 관찰 시각·배치·추적 ID로 고정한다. */
  @Select("""
      SELECT t.batch_id::text,t.session_id::text,t.track_id,t.first_seen,t.last_seen,t.frames,t.max_score,
        t.helmet_frames,t.vest_frames,t.helmet,t.vest,b.rule_version
      FROM detection_tracks t JOIN detection_batches b USING (batch_id)
      WHERE t.device_id=#{deviceId} AND t.camera_id=#{cameraId} AND t.first_seen<#{to} AND t.last_seen>=#{from}
      ORDER BY t.first_seen,t.batch_id,t.track_id LIMIT 1000""")
  List<TrackView> tracks(String deviceId,String cameraId,OffsetDateTime from,OffsetDateTime to);
}
