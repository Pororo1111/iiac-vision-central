package com.example.central;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public final class Models {
  private Models() {}

  public record Admin(String username, String passwordHash, boolean enabled) {}

  public record Device(
      String deviceId,
      String name,
      String modelName,
      String runtimeStatus,
      OffsetDateTime lastSeenAt,
      OffsetDateTime registeredAt,
      boolean online,
      String monitoringAction,
      OffsetDateTime monitoringRequestedAt,
      /** IoT가 heartbeat monitoringCommandId로 현재 감시 명령을 반영했다고 보고했는지. */
      boolean monitoringApplied,
      String lastError,
      OffsetDateTime startedAt,
      String restartTarget,
      OffsetDateTime restartRequestedAt,
      boolean restartApplied,
      /** 중앙 모델 목록에서 찾은 실행 모델 이름. 찾지 못하면 null. */
      String modelDisplayName) {}

  public record MonitoringCommand(String commandId, String action, OffsetDateTime requestedAt) {}

  public record MonitoringRequest(@NotNull @Pattern(regexp = "PAUSE|RESUME") String action) {}

  public record RestartCommand(String commandId, String target, OffsetDateTime requestedAt) {}

  public record RestartRequest(@NotNull @Pattern(regexp = "WORKER|API|ALL") String target) {}

  public record LogRequestCommand(String requestId, int lines, OffsetDateTime requestedAt) {}

  public record LogRequest(@Min(20) @Max(2000) int lines) {}

  public record LogState(String requestId, OffsetDateTime requestedAt, int lines) {}

  public record DeviceLog(
      String service, String requestId, String content, OffsetDateTime collectedAt, OffsetDateTime receivedAt) {}

  public record LogPart(
      @NotBlank @Pattern(regexp = "[A-Za-z0-9_.@-]{1,64}") String service,
      @NotNull @Size(max = 262144) String text) {}

  public record LogUpload(
      @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @NotNull UUID requestId,
      @NotNull OffsetDateTime collectedAt,
      @NotNull @Size(min = 1, max = 6) List<@Valid @NotNull LogPart> logs) {}

  public record DeviceCamera(
      String deviceId,
      String cameraId,
      String displayName,
      String streamStatus,
      Integer width,
      Integer height,
      Double fps,
      OffsetDateTime lastStreamAt,
      String lastError,
      OffsetDateTime updatedAt,
      Integer sourceWidth,
      Integer sourceHeight,
      String sourceDevice) {}

  public record CameraStatus(
      @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String cameraId,
      @NotBlank @Size(max = 120) String displayName,
      @NotBlank @Pattern(regexp = "STARTING|STREAMING|OFFLINE|ERROR") String streamStatus,
      @Min(1) @Max(16384) Integer width,
      @Min(1) @Max(16384) Integer height,
      @DecimalMin("0.1") @DecimalMax("240") Double fps,
      @Size(max = 300) String lastError,
      // 선택: 카메라 장치가 실제로 내는 원본 해상도와 장치 이름. width/height는 송출 해상도다(V35).
      @Min(1) @Max(16384) Integer sourceWidth,
      @Min(1) @Max(16384) Integer sourceHeight,
      @Size(max = 120) String sourceDevice) {
    @AssertTrue(message = "sourceWidth와 sourceHeight는 함께 보낸다")
    public boolean isSourcePair() {
      return (sourceWidth == null) == (sourceHeight == null);
    }
  }

  /** 카메라 원본 장치·해상도 보고 이력(V35). */
  public record CameraSource(
      Integer sourceWidth, Integer sourceHeight, String sourceDevice,
      OffsetDateTime firstSeenAt, OffsetDateTime lastSeenAt) {}

  /** 연속으로 같은 크기였던 원본 사진·수집 이미지 구간. */
  public record SizePeriod(int width, int height, long count, OffsetDateTime firstAt, OffsetDateTime lastAt) {}

  public record CameraSources(
      List<CameraSource> reported, List<SizePeriod> snapshots, List<SizePeriod> samples) {}

  public record ViewSession(String whepUrl, String token, OffsetDateTime expiresAt) {}

  /** heartbeat 응답 streamRequest와 관리자 임대 API 응답. 시각은 UTC다. */
  public record StreamRequest(String cameraId, UUID requestId, OffsetDateTime expiresAt) {}

  public record StreamLeaseRequest(@NotNull UUID viewerId) {}

  public record MediaAuthRequest(
      String user,
      String password,
      String token,
      String ip,
      String action,
      String path,
      String protocol,
      String id,
      String query,
      String userAgent) {}

  public static final String EVENT_TYPES = "INTRUSION_START|INTRUSION_END|FALL_START|FALL_END";

  /**
   * fallCheck는 FALL_START에만 있다: 같은 UUID 탐지 배치의 Gemma 장면 판정으로 PENDING(원본 대기·분석 중),
   * CONFIRMED(fallen&gt;0), NOT_CONFIRMED(fallen=0), UNAVAILABLE(생략·실패). acked*는 관리자 확인 기록.
   */
  public record Event(
      String eventId,
      String deviceId,
      String cameraId,
      String zoneId,
      String type,
      OffsetDateTime occurredAt,
      OffsetDateTime receivedAt,
      int objectCount,
      String fallCheck,
      Integer fallen,
      String checkSummary,
      boolean hasThumbnail,
      OffsetDateTime ackedAt,
      String ackedBy) {}

  public record Registration(
      @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @NotBlank @Size(max = 120) String name) {}

  public record Heartbeat(
      @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @NotNull @Size(max = 200) String modelName,
      @NotBlank @Pattern(regexp = "RUNNING|STOPPED|STALE|ERROR|UNKNOWN") String runtimeStatus,
      /** 현재 활성 모델 파일의 SHA-256. 선택 항목이며 보내지 않으면 이전 값을 유지한다. */
      @Pattern(regexp = "[0-9a-f]{64}") String activeSha256,
      /** 선택: IoT가 마지막으로 적용한 감시 명령 commandId. */
      @Pattern(regexp = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}") String monitoringCommandId,
      /** 선택: IoT가 마지막으로 처리한 재시작 명령 commandId. */
      @Pattern(regexp = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}") String restartCommandId,
      /** 선택: 현재 IoT 오류 요약(카메라 열기 실패 등). 없으면 null. */
      @Size(max = 500) String lastError,
      /** 선택: IoT API 프로세스 기동 시각(UTC). */
      OffsetDateTime startedAt) {}

  public record IncomingEvent(
      @NotNull UUID eventId,
      @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @NotBlank @Size(max = 64) String cameraId,
      /** 침입은 필수. 쓰러짐(FALL_*)은 카메라 전체 판정이라 생략하면 빈 문자열로 저장한다. */
      @Size(max = 64) String zoneId,
      @NotBlank @Pattern(regexp = EVENT_TYPES) String type,
      @NotNull OffsetDateTime occurredAt,
      @NotNull @Min(0) @Max(1000000) Integer objectCount) {}

  public record EventQuery(
      String deviceId,
      String type,
      OffsetDateTime from,
      OffsetDateTime to,
      int size,
      long offset) {}

  public record EventPage(List<Event> items, long total, int page, int size) {}
}
