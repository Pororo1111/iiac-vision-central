package com.example.central;

import com.example.central.Models.*;
import java.time.OffsetDateTime;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.*;
import org.springframework.web.server.ResponseStatusException;

@Service
public class CentralService {
  private final CentralMapper mapper;

  public CentralService(CentralMapper mapper) {
    this.mapper = mapper;
  }

  private void checkDevice(
      String authenticatedId, String requestedId, boolean requireRegistration) {
    if (!authenticatedId.equals(requestedId))
      throw new ResponseStatusException(HttpStatus.FORBIDDEN, "DEVICE_MISMATCH");
    if (requireRegistration && !mapper.registered(authenticatedId))
      throw new ResponseStatusException(HttpStatus.CONFLICT, "DEVICE_NOT_REGISTERED");
  }

  @Transactional
  public void register(String id, Registration body) {
    checkDevice(id, body.deviceId(), false);
    mapper.register(body);
  }

  @Transactional
  public void heartbeat(String id, Heartbeat body) {
    checkDevice(id, body.deviceId(), true);
    mapper.heartbeat(body);
  }

  /** 로그 요청에 대한 응답만 받는다. 같은 요청의 재전송은 저장 없이 성공으로 본다. */
  @Transactional
  public boolean logs(String id, LogUpload body) {
    checkDevice(id, body.deviceId(), true);
    var state = mapper.logState(id);
    String requestId = body.requestId().toString();
    if (state == null || !requestId.equals(state.requestId()))
      throw new ResponseStatusException(HttpStatus.CONFLICT, "LOG_REQUEST_MISMATCH");
    var existing = mapper.logs(id);
    if (!existing.isEmpty() && requestId.equals(existing.get(0).requestId())) return true;
    var services = new java.util.HashSet<String>();
    for (var part : body.logs())
      if (!services.add(part.service()))
        throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST");
    mapper.clearLogs(id);
    for (var part : body.logs())
      mapper.insertLog(id, part.service(), requestId, part.text().replace("\u0000", ""), body.collectedAt());
    return false;
  }

  @Transactional
  public boolean event(String id, IncomingEvent b) {
    checkDevice(id, b.deviceId(), true);
    if (b.occurredAt().isAfter(OffsetDateTime.now().plusMinutes(5)))
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "FUTURE_EVENT");
    int inserted =
        mapper.insertEvent(
            b.eventId().toString(),
            b.deviceId(),
            b.cameraId(),
            b.zoneId(),
            b.type(),
            b.occurredAt(),
            b.objectCount());
    if (inserted == 0 && !id.equals(mapper.eventOwner(b.eventId().toString())))
      throw new ResponseStatusException(HttpStatus.CONFLICT, "EVENT_ID_CONFLICT");
    return inserted == 0;
  }

  @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
  public EventPage events(
      String deviceId, String type, OffsetDateTime from, OffsetDateTime to, int page, int size) {
    if (from != null && to != null && from.isAfter(to))
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "INVALID_DATE_RANGE");
    var query = new EventQuery(deviceId, type, from, to, size, (long) page * size);
    return new EventPage(mapper.events(query), mapper.eventCount(query), page, size);
  }
}
