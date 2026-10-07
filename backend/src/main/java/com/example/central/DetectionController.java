package com.example.central;

import com.example.central.DetectionMapper.*;
import jakarta.validation.Valid;
import java.security.Principal;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.MediaType;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.bind.annotation.*;

@RestController
public class DetectionController {
  final DetectionService service;
  public DetectionController(DetectionService service) { this.service=service; }

  @PostMapping("/api/iot/detections")
  public Map<String,Object> receive(Principal p,@Valid @RequestBody DetectionBatch body) throws Exception {
    return Map.of("accepted",true,"duplicate",service.receive(p.getName(),body,"IOT"),"batchId",body.batchId().toString());
  }

  /** 사람이 나온 배치의 오버레이 없는 원본 프레임(JPEG, 배치당 최대 4장). Gemma 구간 판정에 쓴다. */
  @PostMapping(value="/api/iot/detection-snapshots",consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
  public Map<String,Object> snapshot(Principal p,@RequestParam UUID batchId,@RequestParam String deviceId,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime at,@RequestParam MultipartFile file) throws Exception {
    return Map.of("accepted",true,"duplicate",service.snapshot(p.getName(),batchId,deviceId,at,file));
  }

  /**
   * 중앙 호스트 워커(ai-worker/stream_indexer.py)가 장치 송출 녹화에서 만든 탐지 배치. 워커 Bearer로만 받으며
   * 본문 deviceId의 장치로 IoT 배치와 같은 검증·중복 처리·구간 분석 등록을 한다.
   */
  @PostMapping("/internal/ai/detections")
  public Map<String,Object> receiveFromStream(@Valid @RequestBody DetectionBatch body) throws Exception {
    return Map.of("accepted",true,"duplicate",service.receive(body.deviceId(),body,"STREAM"),"batchId",body.batchId().toString());
  }

  /** 색인 워커용: 이 카메라가 최근 24시간 안에 장치 배치를 보냈으면 송출 녹화를 다시 탐지하지 않는다. */
  @GetMapping("/internal/ai/detections/iot-active")
  public Map<String,Object> iotActive(@RequestParam String deviceId,@RequestParam String cameraId) {
    return Map.of("active",service.iotActive(deviceId,cameraId));
  }

  @GetMapping("/api/detections/tracks")
  public List<TrackView> tracks(@RequestParam String deviceId,@RequestParam String cameraId,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime from,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime to) {
    return service.tracks(deviceId,cameraId,from,to);
  }
}
