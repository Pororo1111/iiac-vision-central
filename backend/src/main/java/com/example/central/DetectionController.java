package com.example.central;

import com.example.central.DetectionMapper.*;
import jakarta.validation.Valid;
import java.security.Principal;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.*;

@RestController
public class DetectionController {
  final DetectionService service;
  public DetectionController(DetectionService service) { this.service=service; }

  @PostMapping("/api/iot/detections")
  public Map<String,Object> receive(Principal p,@Valid @RequestBody DetectionBatch body) throws Exception {
    return Map.of("accepted",true,"duplicate",service.receive(p.getName(),body),"batchId",body.batchId().toString());
  }

  @GetMapping("/api/detections/tracks")
  public List<TrackView> tracks(@RequestParam String deviceId,@RequestParam String cameraId,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime from,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime to) {
    return service.tracks(deviceId,cameraId,from,to);
  }
}
