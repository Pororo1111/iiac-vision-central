package com.example.central;

import com.example.central.Models.StreamRequest;
import java.time.OffsetDateTime;
import java.util.UUID;
import org.apache.ibatis.annotations.*;

@Mapper
public interface StreamLeaseMapper {
  /** 같은 장치·카메라의 임대 변경을 트랜잭션 동안 직렬화한다. */
  @Select("SELECT pg_advisory_xact_lock(hashtext('stream-lease:' || #{deviceId} || '/' || #{cameraId}))")
  Object lock(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  @Delete("DELETE FROM stream_leases WHERE device_id=#{deviceId} AND camera_id=#{cameraId} AND expires_at<=now()")
  int deleteExpiredLease(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  @Delete("DELETE FROM stream_lease_viewers WHERE device_id=#{deviceId} AND camera_id=#{cameraId} AND expires_at<=now()")
  int deleteExpiredViewers(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  /** 이 시청자가 최근 interval 안에 연장했으면 1. 빈도 제한에 쓴다. */
  @Select("SELECT count(*) FROM stream_lease_viewers WHERE device_id=#{deviceId} AND camera_id=#{cameraId}"
      + " AND admin_username=#{admin} AND viewer_id=#{viewerId} AND expires_at>now()"
      + " AND updated_at > now() - make_interval(secs => #{seconds})")
  int recentlyExtended(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId,
      @Param("admin") String admin, @Param("viewerId") UUID viewerId, @Param("seconds") int seconds);

  @Insert("INSERT INTO stream_lease_viewers(device_id,camera_id,admin_username,viewer_id,expires_at)"
      + " VALUES(#{deviceId},#{cameraId},#{admin},#{viewerId},now() + make_interval(secs => #{seconds}))"
      + " ON CONFLICT(device_id,camera_id,admin_username,viewer_id)"
      + " DO UPDATE SET expires_at=excluded.expires_at, updated_at=now()")
  void upsertViewer(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId,
      @Param("admin") String admin, @Param("viewerId") UUID viewerId, @Param("seconds") int seconds);

  /** 만료된 임대는 앞에서 지웠으므로 충돌하면 연장이다. requestId는 새로 만들 때만 정한다. */
  @Insert("INSERT INTO stream_leases(device_id,camera_id,request_id,expires_at,requested_by)"
      + " VALUES(#{deviceId},#{cameraId},#{requestId},now() + make_interval(secs => #{seconds}),#{admin})"
      + " ON CONFLICT(device_id,camera_id) DO UPDATE SET"
      + " expires_at=greatest(stream_leases.expires_at, excluded.expires_at),"
      + " requested_by=excluded.requested_by, updated_at=now()")
  void upsertLease(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId,
      @Param("requestId") UUID requestId, @Param("admin") String admin, @Param("seconds") int seconds);

  @Select("SELECT camera_id,request_id,expires_at FROM stream_leases"
      + " WHERE device_id=#{deviceId} AND camera_id=#{cameraId} AND expires_at>now()")
  StreamRequest lease(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  @Delete("DELETE FROM stream_lease_viewers WHERE device_id=#{deviceId} AND camera_id=#{cameraId}"
      + " AND admin_username=#{admin} AND viewer_id=#{viewerId}")
  int deleteViewer(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId,
      @Param("admin") String admin, @Param("viewerId") UUID viewerId);

  @Select("SELECT max(expires_at) FROM stream_lease_viewers"
      + " WHERE device_id=#{deviceId} AND camera_id=#{cameraId} AND expires_at>now()")
  OffsetDateTime latestViewer(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  @Update("UPDATE stream_leases SET expires_at=#{expiresAt}, updated_at=now()"
      + " WHERE device_id=#{deviceId} AND camera_id=#{cameraId}")
  int shortenLease(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId,
      @Param("expiresAt") OffsetDateTime expiresAt);

  @Delete("DELETE FROM stream_leases WHERE device_id=#{deviceId} AND camera_id=#{cameraId}")
  int deleteLease(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  /** heartbeat 응답용. 장치당 하나만 보내며, 가장 최근에 새로 만든 임대를 고른다(연장으로 순서가 바뀌지 않게). */
  @Select("SELECT camera_id,request_id,expires_at FROM stream_leases"
      + " WHERE device_id=#{deviceId} AND expires_at>now() ORDER BY created_at DESC, camera_id LIMIT 1")
  StreamRequest current(String deviceId);

  @Delete("DELETE FROM stream_leases WHERE expires_at<=now()")
  int purgeLeases();

  @Delete("DELETE FROM stream_lease_viewers WHERE expires_at<=now()")
  int purgeViewers();
}
