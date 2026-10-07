package com.example.central;

import com.example.central.Models.StreamRequest;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/**
 * 관리자가 카메라 보기를 열어 둔 동안에만 IoT가 송출하도록 하는 송출 임대.
 * 중앙은 IoT에 접속하지 않고 heartbeat 응답 streamRequest로만 전달한다.
 */
@Service
public class StreamLeaseService {
  private final StreamLeaseMapper db;
  private final CentralMapper central;
  private final int ttlSeconds;
  private final int minExtendSeconds;

  public StreamLeaseService(StreamLeaseMapper db, CentralMapper central,
      @Value("${app.stream-lease.ttl-seconds:60}") int ttlSeconds,
      @Value("${app.stream-lease.min-extend-seconds:10}") int minExtendSeconds) {
    this.db = db;
    this.central = central;
    this.ttlSeconds = ttlSeconds;
    this.minExtendSeconds = minExtendSeconds;
  }

  /** 임대 생성·연장. 같은 시청자가 min-extend-seconds 안에 다시 요청하면 연장하지 않고 현재 임대를 돌려준다. */
  @Transactional
  public StreamRequest acquire(String deviceId, String cameraId, String admin, UUID viewerId) {
    if (central.device(deviceId) == null || central.camera(deviceId, cameraId) == null) {
      throw new ResponseStatusException(HttpStatus.NOT_FOUND, "CAMERA_NOT_FOUND");
    }
    db.lock(deviceId, cameraId);
    db.deleteExpiredLease(deviceId, cameraId);
    db.deleteExpiredViewers(deviceId, cameraId);
    if (db.recentlyExtended(deviceId, cameraId, admin, viewerId, minExtendSeconds) > 0) {
      var lease = db.lease(deviceId, cameraId);
      if (lease != null) return lease;
    }
    db.upsertViewer(deviceId, cameraId, admin, viewerId, ttlSeconds);
    db.upsertLease(deviceId, cameraId, UUID.randomUUID(), admin, ttlSeconds);
    return db.lease(deviceId, cameraId);
  }

  /** 이 시청자만 해제한다. 남은 시청자가 없으면 임대를 지우고, 있으면 남은 시청자의 만료로 줄인다. */
  @Transactional
  public void release(String deviceId, String cameraId, String admin, UUID viewerId) {
    if (central.device(deviceId) == null || central.camera(deviceId, cameraId) == null) {
      throw new ResponseStatusException(HttpStatus.NOT_FOUND, "CAMERA_NOT_FOUND");
    }
    db.lock(deviceId, cameraId);
    db.deleteViewer(deviceId, cameraId, admin, viewerId);
    var latest = db.latestViewer(deviceId, cameraId);
    if (latest == null) db.deleteLease(deviceId, cameraId);
    else db.shortenLease(deviceId, cameraId, latest);
  }

  public StreamRequest current(String deviceId) {
    return db.current(deviceId);
  }

  @Scheduled(initialDelay = 30000, fixedDelayString = "${app.stream-lease.purge-interval-ms:60000}")
  public void purge() {
    db.purgeLeases();
    db.purgeViewers();
  }
}
