package com.example.central;

import com.example.central.FpCheckService.*;
import jakarta.validation.Valid;
import java.security.Principal;
import java.util.UUID;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/** 오탐 확인(V33)·관리자 검토(V34). 워커 API는 loopback 워커 Bearer, 조회·검토는 관리자 세션(+CSRF). */
@RestController
public class FpCheckController {
  final FpCheckService service;
  public FpCheckController(FpCheckService service) { this.service=service; }

  @PostMapping("/internal/ai/fp-checks/claim")
  public ResponseEntity<Job> claim(@RequestParam(required=false) String deviceId) {
    var job=service.claim(deviceId);
    return job==null?ResponseEntity.noContent().build():ResponseEntity.ok(job);
  }
  @PostMapping("/internal/ai/fp-checks/{id}/complete")
  public void complete(@PathVariable UUID id,@Valid @RequestBody Completion body) { service.complete(id,body); }
  @PostMapping("/internal/ai/fp-checks/{id}/fail")
  public void fail(@PathVariable UUID id,@Valid @RequestBody Failure body) { service.fail(id,body); }
  @PostMapping("/internal/ai/fp-checks/{id}/release")
  public void release(@PathVariable UUID id,@Valid @RequestBody Failure body) { service.release(id,body); }

  @GetMapping("/api/training/fp-checks")
  public View recent(@RequestParam(required=false) String review,@RequestParam(defaultValue="50") int limit) {
    return service.recent(review,limit);
  }
  @GetMapping("/api/training/fp-checks/{id}")
  public Detail detail(@PathVariable UUID id) { return service.detail(id); }
  @GetMapping("/api/training/fp-checks/{id}/image")
  public ResponseEntity<FileSystemResource> image(@PathVariable UUID id) {
    return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG).cacheControl(CacheControl.noStore().cachePrivate())
        .body(new FileSystemResource(service.image(id)));
  }
  @PostMapping("/api/training/fp-checks/{id}/review")
  public ReviewResult review(Principal p,@PathVariable UUID id,@Valid @RequestBody ReviewRequest body) throws Exception {
    return service.review(p.getName(),id,body);
  }
}
