package com.example.central;

import com.example.central.Models.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class ApiController {
  private final CentralMapper mapper;
  private final CentralService service;
  private final DeploymentService deployments;
  private final StreamLeaseService streams;

  public ApiController(CentralMapper mapper, CentralService service, DeploymentService deployments,
      StreamLeaseService streams) {
    this.mapper = mapper;
    this.service = service;
    this.deployments = deployments;
    this.streams = streams;
  }

  @GetMapping("/health")
  public Map<String, String> health() {
    return Map.of("status", "UP");
  }

  @GetMapping("/auth/csrf")
  public Map<String, String> csrf(CsrfToken token) {
    return Map.of("headerName", token.getHeaderName(), "token", token.getToken());
  }

  @GetMapping("/auth/me")
  public Map<String, String> me(Principal principal) {
    return Map.of("username", principal.getName());
  }

  @GetMapping("/devices")
  public List<Device> devices() {
    return mapper.devices();
  }

  @GetMapping("/events")
  public EventPage events(
      @RequestParam(required = false) @Size(max = 64) String deviceId,
      @RequestParam(required = false) @Pattern(regexp = Models.EVENT_TYPES)
          String type,
      @RequestParam(required = false) OffsetDateTime from,
      @RequestParam(required = false) OffsetDateTime to,
      @RequestParam(defaultValue = "0") @Min(0) @Max(1000000) int page,
      @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
    return service.events(deviceId, type, from, to, page, size);
  }

  /** 관리자가 확인하지 않은 최근 24시간 쓰러짐(FALL_START), 최신순 최대 20건. */
  @GetMapping("/alerts/falls")
  public List<Event> openFalls() {
    return service.openFalls();
  }

  @PostMapping("/events/{eventId}/ack")
  public Event ackFall(Principal p, @PathVariable UUID eventId) {
    return service.ackFall(eventId.toString(), p.getName());
  }

  @PostMapping("/iot/register")
  public Map<String, Boolean> register(Principal p, @Valid @RequestBody Registration body) {
    service.register(p.getName(), body);
    return Map.of("registered", true);
  }

  /**
   * 응답의 monitoring은 중앙이 요청한 최신 감시 명령(없으면 null)이다. IoT는 처음 보는 commandId만 적용한다.
   * deployment는 이 장치의 미완료 배포(없으면 null)로 GET /iot/deployments/desired와 같은 형식이다.
   * streamRequest는 관리자가 시청 중인 카메라의 송출 임대(없으면 null)다. 장치당 하나만 보낸다.
   */
  @PostMapping("/iot/heartbeat")
  public Map<String, Object> heartbeat(Principal p, @Valid @RequestBody Heartbeat body) {
    service.heartbeat(p.getName(), body);
    var response = new java.util.LinkedHashMap<String, Object>();
    response.put("accepted", true);
    response.put("monitoring", mapper.monitoringCommand(p.getName()));
    response.put("deployment", deployments.desired(p.getName()));
    response.put("restart", mapper.restartCommand(p.getName()));
    response.put("logRequest", mapper.logRequestCommand(p.getName()));
    response.put("streamRequest", streams.current(p.getName()));
    return response;
  }

  /** heartbeat 응답 logRequest에 대한 IoT의 로그 업로드. 장치별 마지막 응답만 보관한다. */
  @PostMapping("/iot/logs")
  public Map<String, Object> logs(Principal p, @Valid @RequestBody LogUpload body) {
    return Map.of("accepted", true, "duplicate", service.logs(p.getName(), body));
  }

  @GetMapping("/devices/{id}")
  public Device device(@PathVariable @Size(max = 64) String id) {
    return found(mapper.device(id));
  }

  /** 재시작은 연결된 장치에만 요청할 수 있다. 2분 안에 전달되지 않으면 만료된다. */
  @PostMapping("/devices/{id}/restart")
  public Device restart(@PathVariable @Size(max = 64) String id, @Valid @RequestBody RestartRequest body, Principal p) {
    requireOnline(found(mapper.device(id)));
    mapper.requestRestart(id, body.target(), p.getName());
    return mapper.device(id);
  }

  @PostMapping("/devices/{id}/logs/request")
  public Map<String, Object> requestLogs(@PathVariable @Size(max = 64) String id, @Valid @RequestBody LogRequest body) {
    requireOnline(found(mapper.device(id)));
    mapper.requestLogs(id, body.lines());
    return deviceLogs(id);
  }

  @GetMapping("/devices/{id}/logs")
  public Map<String, Object> deviceLogs(@PathVariable @Size(max = 64) String id) {
    found(mapper.device(id));
    var state = mapper.logState(id);
    var logs = mapper.logs(id);
    var response = new LinkedHashMap<String, Object>();
    response.put("request", state);
    response.put("pending", state != null && (logs.isEmpty() || !state.requestId().equals(logs.get(0).requestId())));
    response.put("logs", logs);
    return response;
  }

  private static Device found(Device device) {
    if (device == null)
      throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND, "DEVICE_NOT_FOUND");
    return device;
  }

  private static void requireOnline(Device device) {
    if (!device.online())
      throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.CONFLICT, "DEVICE_OFFLINE");
  }

  @PostMapping("/devices/{id}/monitoring")
  public Device monitoring(@PathVariable @Size(max = 64) String id, @Valid @RequestBody MonitoringRequest body, Principal p) {
    if (mapper.requestMonitoring(id, body.action(), p.getName()) != 1)
      throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND, "DEVICE_NOT_FOUND");
    return mapper.device(id);
  }

  @PostMapping("/iot/events")
  public Map<String, Object> event(Principal p, @Valid @RequestBody IncomingEvent body) {
    return Map.of(
        "accepted",
        true,
        "duplicate",
        service.event(p.getName(), body),
        "eventId",
        body.eventId().toString());
  }
}
