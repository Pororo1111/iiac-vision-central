package com.example.central;

import com.example.central.Models.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Pattern;
import java.security.Principal;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

@RestController
@Validated
public class MediaController {
  private final CentralMapper mapper;
  private final MediaService media;
  private final StreamLeaseService streams;

  public MediaController(CentralMapper mapper, MediaService media, StreamLeaseService streams) {
    this.mapper = mapper;
    this.media = media;
    this.streams = streams;
  }

  @PostMapping("/api/iot/cameras/status")
  public Map<String, Boolean> cameraStatus(Principal principal, @Valid @RequestBody CameraStatus status) {
    if (!principal.getName().equals(status.deviceId())) {
      throw new org.springframework.web.server.ResponseStatusException(
          org.springframework.http.HttpStatus.FORBIDDEN, "DEVICE_MISMATCH");
    }
    media.report(status);
    return Map.of("accepted", true);
  }

  @GetMapping("/api/devices/{deviceId}/cameras")
  public List<DeviceCamera> cameras(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId) {
    return mapper.cameras(deviceId);
  }

  /** 원본 장치·해상도 보고 이력과 원본 사진·수집 이미지 크기 구간. 장치 교체 시점 확인용이다. */
  @GetMapping("/api/devices/{deviceId}/cameras/{cameraId}/sources")
  public CameraSources sources(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String cameraId) {
    if (mapper.camera(deviceId, cameraId) == null) {
      throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND);
    }
    return new CameraSources(mapper.cameraSources(deviceId, cameraId),
        mapper.snapshotSizes(deviceId, cameraId), mapper.sampleSizes(deviceId, cameraId));
  }

  @PostMapping("/api/devices/{deviceId}/cameras/{cameraId}/view-session")
  public ViewSession view(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String cameraId) {
    return media.create(deviceId, cameraId);
  }

  /** 카메라 보기를 연 창(viewerId)의 송출 임대 생성·연장. 화면은 30초마다 연장한다. */
  @PostMapping("/api/devices/{deviceId}/cameras/{cameraId}/stream-lease")
  public StreamRequest acquireLease(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String cameraId,
      @Valid @RequestBody StreamLeaseRequest body,
      Principal principal) {
    return streams.acquire(deviceId, cameraId, principal.getName(), body.viewerId());
  }

  @DeleteMapping("/api/devices/{deviceId}/cameras/{cameraId}/stream-lease")
  public ResponseEntity<Void> releaseLease(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String cameraId,
      @RequestParam java.util.UUID viewerId,
      Principal principal) {
    streams.release(deviceId, cameraId, principal.getName(), viewerId);
    return ResponseEntity.noContent().build();
  }

  @PostMapping("/internal/media/auth")
  public ResponseEntity<Void> authenticate(@RequestBody MediaAuthRequest request) {
    return media.authorize(request)
        ? ResponseEntity.noContent().build()
        : ResponseEntity.status(401).build();
  }
}
