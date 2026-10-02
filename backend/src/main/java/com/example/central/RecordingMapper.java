package com.example.central;

import java.time.OffsetDateTime;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface RecordingMapper {
  record Recording(String id, String deviceId, String cameraId, OffsetDateTime startedAt, OffsetDateTime endedAt,
      double durationSeconds, String source, String sha256, long sizeBytes, OffsetDateTime createdAt) {}
  record Located(Recording recording, double offsetSeconds, String playbackPath) {}

  String COLUMNS="""
      id::text,device_id,camera_id,started_at,ended_at,
      extract(epoch FROM ended_at-started_at)::float8 AS duration_seconds,source,sha256,size_bytes,created_at""";

  @Select(value="""
      INSERT INTO recordings(id,device_id,camera_id,started_at,ended_at,sha256,size_bytes)
      VALUES(#{id}::uuid,#{deviceId},#{cameraId},#{startedAt},#{endedAt},#{sha256},#{sizeBytes})
      ON CONFLICT (device_id,camera_id,started_at) DO NOTHING RETURNING id::text""",affectData=true)
  String insert(String id,String deviceId,String cameraId,OffsetDateTime startedAt,OffsetDateTime endedAt,
      String sha256,long sizeBytes);

  @Select("SELECT "+COLUMNS+" FROM recordings WHERE id=#{id}::uuid") Recording view(String id);

  @Select("SELECT "+COLUMNS+" FROM recordings WHERE device_id=#{deviceId} AND camera_id=#{cameraId} AND started_at=#{startedAt}")
  Recording byStart(String deviceId,String cameraId,OffsetDateTime startedAt);

  @Select("SELECT "+COLUMNS+" FROM recordings WHERE (#{deviceId}::text IS NULL OR device_id=#{deviceId})"
      +" AND (#{cameraId}::text IS NULL OR camera_id=#{cameraId}) ORDER BY started_at DESC,id LIMIT 100")
  List<Recording> list(String deviceId,String cameraId);

  /** 겹친 녹화가 있으면 가장 늦게 시작한 것을 고른다. 종료 시각은 포함하지 않는다([start,end)). */
  @Select("SELECT "+COLUMNS+" FROM recordings WHERE device_id=#{deviceId} AND camera_id=#{cameraId}"
      +" AND started_at<=#{at} AND ended_at>#{at} ORDER BY started_at DESC,id LIMIT 1")
  Recording covering(String deviceId,String cameraId,OffsetDateTime at);

  @Select("SELECT EXISTS(SELECT 1 FROM devices WHERE device_id=#{deviceId})") boolean deviceExists(String deviceId);
}
