package com.example.central;

import com.example.central.RecordingMapper.*;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.core.io.FileSystemResource;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

@RestController
public class RecordingController {
  final RecordingService service;
  public RecordingController(RecordingService service) { this.service=service; }

  @PostMapping(value="/internal/ai/recordings",consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
  public Recording register(@RequestParam MultipartFile file,@RequestParam String deviceId,@RequestParam String cameraId,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime startedAt,
      @RequestParam double durationSeconds) throws Exception {
    return service.register(file,deviceId,cameraId,startedAt,durationSeconds);
  }

  @GetMapping("/api/recordings")
  public List<Recording> list(@RequestParam(required=false) String deviceId,@RequestParam(required=false) String cameraId) {
    return service.list(deviceId,cameraId);
  }
  @GetMapping("/api/recordings/locate")
  public Located locate(@RequestParam String deviceId,@RequestParam String cameraId,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime at) {
    return service.locate(deviceId,cameraId,at);
  }
  @GetMapping("/api/recordings/{id}") public Recording view(@PathVariable UUID id) { return service.view(id); }

  /** Range 요청을 지원해 브라우저가 필요한 위치부터 재생한다. */
  @GetMapping("/api/recordings/{id}/video")
  public ResponseEntity<FileSystemResource> video(@PathVariable UUID id) {
    return ResponseEntity.ok().contentType(MediaType.parseMediaType("video/mp4"))
        .cacheControl(CacheControl.noStore().cachePrivate())
        .body(new FileSystemResource(service.video(id)));
  }
}
