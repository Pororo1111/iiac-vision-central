package com.example.central;

import com.example.central.FpMemoryService.*;
import jakarta.validation.Valid;
import java.util.List;
import java.util.UUID;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/** 오탐 판단 기억(V37). 워커 API는 loopback 워커 Bearer, 근거 사진 조회는 관리자 세션. */
@RestController
public class FpMemoryController {
  final FpMemoryService service;
  public FpMemoryController(FpMemoryService service) { this.service=service; }

  @GetMapping("/internal/ai/fp-memory/pending")
  public List<PendingView> pending() { return service.pending(); }
  @PostMapping("/internal/ai/fp-memory/{id}/embedding")
  public void embed(@PathVariable UUID id,@Valid @RequestBody Embedding body) { service.embed(id,body); }
  @PostMapping("/internal/ai/fp-memory/seed")
  public Saved seed(@Valid @RequestBody Seed body) { return service.seed(body); }
  @PostMapping("/internal/ai/fp-memory/search")
  public List<Found> search(@Valid @RequestBody Search body) { return service.search(body); }

  @GetMapping("/api/training/fp-memory/{id}/image")
  public ResponseEntity<FileSystemResource> image(@PathVariable UUID id) {
    return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG).cacheControl(CacheControl.noStore().cachePrivate())
        .body(new FileSystemResource(service.crop(id)));
  }
}
