package com.example.central;

import com.example.central.Models.*;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface CentralMapper {
  String DEVICE_STATUS =
      "COALESCE(last_seen_at > now() - interval '30 seconds',false) AS online,monitoring_action,monitoring_requested_at,"
          + " COALESCE(monitoring_applied_id=monitoring_command_id,false) AS monitoring_applied,last_error,"
          + " iot_started_at AS started_at,restart_target,restart_requested_at,"
          + " COALESCE(restart_applied_id=restart_command_id,false) AS restart_applied,"
          // IoT가 보고한 model_name은 배포 모델 UUID일 수 있어 활성 해시 또는 UUID로 모델 이름을 찾는다.
          + " (SELECT COALESCE(m.label,(SELECT p.name FROM pipelines p WHERE p.run_id=m.id ORDER BY p.created_at DESC LIMIT 1),r.name)"
          + "  FROM model_releases m LEFT JOIN training_runs r ON r.id=m.id"
          + "  WHERE m.sha256=devices.active_sha256 OR m.id::text=devices.model_name"
          + "  ORDER BY (m.sha256=devices.active_sha256) DESC,m.created_at DESC LIMIT 1) AS model_display_name";

  @Select("SELECT username, password_hash, enabled FROM admins WHERE username=#{username}")
  Admin admin(String username);

  @Insert(
      "INSERT INTO admins(username,password_hash) VALUES(#{username},#{hash}) ON CONFLICT(username)"
          + " DO NOTHING")
  void seedAdmin(@Param("username") String username, @Param("hash") String hash);

  @Insert(
      "INSERT INTO devices(device_id,name,token_hash) VALUES(#{id},#{id},#{hash}) ON"
          + " CONFLICT(device_id) DO NOTHING")
  void seedDevice(@Param("id") String id, @Param("hash") String hash);

  @Select("SELECT device_id FROM devices WHERE token_hash=#{hash}")
  String deviceByToken(String hash);

  @Select("SELECT EXISTS(SELECT 1 FROM devices WHERE device_id=#{id} AND registered_at IS NOT NULL)")
  boolean registered(String id);

  @Update(
      "UPDATE devices SET name=#{name}, registered_at=COALESCE(registered_at,now()),"
          + " last_seen_at=now() WHERE device_id=#{deviceId}")
  void register(Registration body);

  @Update(
      "UPDATE devices SET model_name=#{modelName}, runtime_status=#{runtimeStatus},"
          + " active_sha256=COALESCE(#{activeSha256},active_sha256),"
          + " monitoring_applied_id=COALESCE(CAST(#{monitoringCommandId} AS uuid),monitoring_applied_id),"
          + " restart_applied_id=COALESCE(CAST(#{restartCommandId} AS uuid),restart_applied_id),"
          + " last_error=#{lastError}, iot_started_at=COALESCE(#{startedAt},iot_started_at),"
          + " last_seen_at=now() WHERE device_id=#{deviceId}")
  void heartbeat(Heartbeat body);

  @Select(
      "SELECT device_id,name,model_name,runtime_status,last_seen_at,registered_at,"
          + " " + DEVICE_STATUS + " FROM devices WHERE registered_at IS NOT NULL"
          + " ORDER BY created_at,device_id")
  List<Device> devices();

  @Select(
      "SELECT device_id,name,model_name,runtime_status,last_seen_at,registered_at,"
          + " " + DEVICE_STATUS
          + " FROM devices WHERE device_id=#{id} AND registered_at IS NOT NULL")
  Device device(String id);

  @Update(
      "UPDATE devices SET monitoring_action=#{action},monitoring_command_id=gen_random_uuid(),monitoring_requested_at=now(),"
          + " monitoring_requested_by=#{user} WHERE device_id=#{id} AND registered_at IS NOT NULL")
  int requestMonitoring(String id, String action, String user);

  @Select(
      "SELECT monitoring_command_id::text AS command_id,monitoring_action AS action,monitoring_requested_at AS requested_at"
          + " FROM devices WHERE device_id=#{id} AND monitoring_command_id IS NOT NULL")
  Models.MonitoringCommand monitoringCommand(String id);

  @Update(
      "UPDATE devices SET restart_target=#{target},restart_command_id=gen_random_uuid(),restart_requested_at=now(),"
          + " restart_requested_by=#{user} WHERE device_id=#{id} AND registered_at IS NOT NULL")
  int requestRestart(String id, String target, String user);

  /** 오래된 재시작 명령이 나중에 연결된 장치에서 갑자기 실행되지 않도록 2분 안의 명령만 전달한다. */
  @Select(
      "SELECT restart_command_id::text AS command_id,restart_target AS target,restart_requested_at AS requested_at"
          + " FROM devices WHERE device_id=#{id} AND restart_command_id IS NOT NULL"
          + " AND restart_applied_id IS DISTINCT FROM restart_command_id AND restart_requested_at > now() - interval '2 minutes'")
  Models.RestartCommand restartCommand(String id);

  @Update(
      "UPDATE devices SET log_request_id=gen_random_uuid(),log_requested_at=now(),log_lines=#{lines}"
          + " WHERE device_id=#{id} AND registered_at IS NOT NULL")
  int requestLogs(String id, int lines);

  @Select("SELECT log_request_id::text AS request_id,log_requested_at AS requested_at,COALESCE(log_lines,0) AS lines"
      + " FROM devices WHERE device_id=#{id} AND log_request_id IS NOT NULL")
  Models.LogState logState(String id);

  /** 아직 받지 못한 2분 안의 로그 요청만 전달한다. */
  @Select(
      "SELECT d.log_request_id::text AS request_id,d.log_lines AS lines,d.log_requested_at AS requested_at FROM devices d"
          + " WHERE d.device_id=#{id} AND d.log_request_id IS NOT NULL AND d.log_requested_at > now() - interval '2 minutes'"
          + " AND NOT EXISTS (SELECT 1 FROM device_logs l WHERE l.device_id=d.device_id AND l.request_id=d.log_request_id)")
  Models.LogRequestCommand logRequestCommand(String id);

  @Delete("DELETE FROM device_logs WHERE device_id=#{id}")
  void clearLogs(String id);

  @Insert(
      "INSERT INTO device_logs(device_id,service,request_id,content,collected_at)"
          + " VALUES(#{id},#{service},CAST(#{requestId} AS uuid),#{content},#{collectedAt})")
  void insertLog(
      @Param("id") String id,
      @Param("service") String service,
      @Param("requestId") String requestId,
      @Param("content") String content,
      @Param("collectedAt") java.time.OffsetDateTime collectedAt);

  @Select(
      "SELECT service,request_id::text AS request_id,content,collected_at,received_at FROM device_logs"
          + " WHERE device_id=#{id} ORDER BY service")
  List<Models.DeviceLog> logs(String id);

  @Insert(
      "INSERT INTO device_cameras(device_id,camera_id,display_name,stream_status,width,height,fps,last_stream_at,last_error,"
          + "source_width,source_height,source_device)"
          + " VALUES(#{deviceId},#{cameraId},#{displayName},#{streamStatus},#{width},#{height},#{fps},"
          + " CASE WHEN #{streamStatus}='STREAMING' THEN now() ELSE NULL END,#{lastError},"
          + "#{sourceWidth},#{sourceHeight},#{sourceDevice})"
          + " ON CONFLICT(device_id,camera_id) DO UPDATE SET display_name=excluded.display_name,"
          + " stream_status=excluded.stream_status,width=excluded.width,height=excluded.height,fps=excluded.fps,"
          + " last_stream_at=CASE WHEN excluded.stream_status='STREAMING' THEN now() ELSE device_cameras.last_stream_at END,"
          + " last_error=excluded.last_error,updated_at=now(),"
          // 원본 필드를 보내지 않는 IoT(이전 버전)는 마지막 보고값을 지우지 않는다.
          + " source_width=COALESCE(excluded.source_width,device_cameras.source_width),"
          + " source_height=COALESCE(excluded.source_height,device_cameras.source_height),"
          + " source_device=CASE WHEN excluded.source_width IS NULL THEN device_cameras.source_device"
          + " ELSE excluded.source_device END")
  void upsertCamera(CameraStatus status);

  /** 원본 보고값이 최근 이력과 같으면 last_seen_at만 갱신하고, 다르면(장치 교체) 새 행을 만든다. */
  @Update(
      "UPDATE camera_source_history SET last_seen_at=now() WHERE id=(SELECT id FROM camera_source_history"
          + " WHERE device_id=#{deviceId} AND camera_id=#{cameraId} ORDER BY first_seen_at DESC,id DESC LIMIT 1)"
          + " AND source_width=#{sourceWidth} AND source_height=#{sourceHeight}"
          + " AND source_device IS NOT DISTINCT FROM #{sourceDevice}")
  int touchCameraSource(CameraStatus status);

  @Insert(
      "INSERT INTO camera_source_history(device_id,camera_id,source_width,source_height,source_device)"
          + " VALUES(#{deviceId},#{cameraId},#{sourceWidth},#{sourceHeight},#{sourceDevice})")
  void insertCameraSource(CameraStatus status);

  @Select(
      "SELECT source_width,source_height,source_device,first_seen_at,last_seen_at FROM camera_source_history"
          + " WHERE device_id=#{deviceId} AND camera_id=#{cameraId} ORDER BY first_seen_at DESC,id DESC LIMIT 10")
  List<Models.CameraSource> cameraSources(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  /** 장치가 보낸 원본 사진(긴 변 768px 축소본) 크기가 연속으로 같았던 구간, 최근 10개. */
  @Select(
      "WITH s AS (SELECT s.width,s.height,s.captured_at AS at FROM detection_snapshots s"
          + " JOIN detection_batches b USING(batch_id) WHERE b.device_id=#{deviceId} AND b.camera_id=#{cameraId}),"
          + " g AS (SELECT *,CASE WHEN (width,height) IS DISTINCT FROM"
          + " (LAG(width) OVER (ORDER BY at),LAG(height) OVER (ORDER BY at)) THEN 1 ELSE 0 END AS changed FROM s),"
          + " r AS (SELECT *,SUM(changed) OVER (ORDER BY at ROWS UNBOUNDED PRECEDING) AS grp FROM g)"
          + " SELECT width,height,COUNT(*) AS count,MIN(at) AS first_at,MAX(at) AS last_at FROM r"
          + " GROUP BY grp,width,height ORDER BY first_at DESC LIMIT 10")
  List<Models.SizePeriod> snapshotSizes(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  /** 이 카메라에서 들어온 학습용 수집 이미지 크기가 연속으로 같았던 구간, 최근 10개. */
  @Select(
      "WITH s AS (SELECT width,height,COALESCE(captured_at,created_at) AS at FROM training_samples"
          + " WHERE device_id=#{deviceId} AND camera_id=#{cameraId}),"
          + " g AS (SELECT *,CASE WHEN (width,height) IS DISTINCT FROM"
          + " (LAG(width) OVER (ORDER BY at),LAG(height) OVER (ORDER BY at)) THEN 1 ELSE 0 END AS changed FROM s),"
          + " r AS (SELECT *,SUM(changed) OVER (ORDER BY at ROWS UNBOUNDED PRECEDING) AS grp FROM g)"
          + " SELECT width,height,COUNT(*) AS count,MIN(at) AS first_at,MAX(at) AS last_at FROM r"
          + " GROUP BY grp,width,height ORDER BY first_at DESC LIMIT 10")
  List<Models.SizePeriod> sampleSizes(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  @Select(
      "SELECT device_id,camera_id,display_name,stream_status,width,height,fps,last_stream_at,last_error,updated_at,"
          + "source_width,source_height,source_device"
          + " FROM device_cameras WHERE device_id=#{deviceId} ORDER BY camera_id")
  List<DeviceCamera> cameras(String deviceId);

  @Select(
      "SELECT device_id,camera_id,display_name,stream_status,width,height,fps,last_stream_at,last_error,updated_at,"
          + "source_width,source_height,source_device"
          + " FROM device_cameras WHERE device_id=#{deviceId} AND camera_id=#{cameraId}")
  DeviceCamera camera(@Param("deviceId") String deviceId, @Param("cameraId") String cameraId);

  @Insert(
      "INSERT INTO events(event_id,device_id,camera_id,zone_id,type,occurred_at,object_count)"
          + " VALUES(CAST(#{eventId} AS"
          + " uuid),#{deviceId},#{cameraId},#{zoneId},#{type},#{occurredAt},#{objectCount}) ON"
          + " CONFLICT(event_id) DO NOTHING")
  int insertEvent(
      @Param("eventId") String eventId,
      @Param("deviceId") String deviceId,
      @Param("cameraId") String cameraId,
      @Param("zoneId") String zoneId,
      @Param("type") String type,
      @Param("occurredAt") java.time.OffsetDateTime occurredAt,
      @Param("objectCount") int objectCount);

  @Select("SELECT device_id FROM events WHERE event_id=CAST(#{id} AS uuid)")
  String eventOwner(String id);

  String FILTER =
      "<where><if test='deviceId != null'>e.device_id=#{deviceId}</if>"
          + "<if test='type != null'> AND e.type=#{type}</if>"
          + "<if test='from != null'> AND e.occurred_at &gt;= #{from}</if>"
          + "<if test='to != null'> AND e.occurred_at &lt;= #{to}</if></where>";

  /** FALL_START는 같은 UUID의 탐지 배치 구간 판정(observations)을 Gemma 2차 확인으로 붙인다. */
  String EVENT_COLUMNS =
      "e.event_id::text AS event_id,e.device_id,e.camera_id,e.zone_id,e.type,e.occurred_at,e.received_at,"
          + "e.object_count,"
          + "CASE WHEN e.type&lt;&gt;'FALL_START' THEN NULL"
          + " WHEN o.batch_id IS NULL OR o.status IN ('QUEUED','RUNNING') THEN 'PENDING'"
          + " WHEN o.status='COMPLETED' AND coalesce(o.fallen,0)&gt;0 THEN 'CONFIRMED'"
          + " WHEN o.status='COMPLETED' THEN 'NOT_CONFIRMED' ELSE 'UNAVAILABLE' END AS fall_check,"
          + "CASE WHEN e.type='FALL_START' THEN o.fallen END AS fallen,"
          + "CASE WHEN e.type='FALL_START' THEN o.summary END AS check_summary,"
          + "e.type='FALL_START' AND coalesce(o.has_thumbnail,false) AS has_thumbnail,"
          + "a.acked_at,a.acked_by"
          + " FROM events e LEFT JOIN observations o ON o.batch_id=e.event_id"
          + " LEFT JOIN event_acks a ON a.event_id=e.event_id ";

  @Select(
      "<script>SELECT "
          + EVENT_COLUMNS
          + FILTER
          + " ORDER BY e.occurred_at DESC,e.event_id DESC LIMIT #{size} OFFSET #{offset}</script>")
  List<Event> events(EventQuery query);

  @Select("<script>SELECT count(*) FROM events e " + FILTER + "</script>")
  long eventCount(EventQuery query);

  /** 관리자가 확인하지 않은 최근 쓰러짐(FALL_START). */
  @Select(
      "<script>SELECT "
          + EVENT_COLUMNS
          + " WHERE e.type='FALL_START' AND a.event_id IS NULL AND e.occurred_at &gt;= #{since}"
          + " ORDER BY e.occurred_at DESC,e.event_id DESC LIMIT #{limit}</script>")
  List<Event> openFalls(
      @Param("since") java.time.OffsetDateTime since, @Param("limit") int limit);

  @Select("<script>SELECT " + EVENT_COLUMNS + " WHERE e.event_id=CAST(#{id} AS uuid)</script>")
  Event event(String id);

  @Insert(
      "INSERT INTO event_acks(event_id,acked_by) VALUES(CAST(#{id} AS uuid),#{admin})"
          + " ON CONFLICT(event_id) DO NOTHING")
  int ackEvent(@Param("id") String id, @Param("admin") String admin);
}
