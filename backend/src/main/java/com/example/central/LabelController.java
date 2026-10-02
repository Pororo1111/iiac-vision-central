package com.example.central;

import com.example.central.LabelModels.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class LabelController {
  final LabelService service;
  final LabelMapper db;
  final CentralMapper devices;
  public LabelController(LabelService service,LabelMapper db,CentralMapper devices) { this.service=service;this.db=db;this.devices=devices; }
  @GetMapping("/api/training/samples") public SamplePage samples(@RequestParam(defaultValue="0") @Min(0) @Max(100000) int page,
      @RequestParam(required=false) @Size(max=64) String deviceId,
      @RequestParam(required=false) @Pattern(regexp="NEW|REVIEW|APPROVED|EXCLUDED|LABELED") String status) {
    return new SamplePage(db.samples(page*40,deviceId,status),db.count(deviceId,status),page,40);
  }
  @GetMapping("/api/training/stats") public Stats stats() { db.expire(); return db.stats(); }
  @GetMapping("/api/training/samples/{id}") public Sample sample(@PathVariable UUID id) { return service.sample(id.toString()); }
  @PostMapping(value="/api/iot/training-samples",consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
  public Sample iotUpload(Principal principal,@RequestParam MultipartFile file,
      @RequestParam @NotBlank @Size(max=64) String deviceId,@RequestParam @NotBlank @Size(max=64) String cameraId,
      @RequestParam OffsetDateTime capturedAt,@RequestParam @NotBlank @Size(max=160) String title) throws Exception {
    if(!principal.getName().equals(deviceId)) throw new ResponseStatusException(HttpStatus.FORBIDDEN,"DEVICE_MISMATCH");
    LabelService.require(devices.registered(deviceId),"DEVICE_NOT_REGISTERED");
    if(capturedAt.isAfter(OffsetDateTime.now().plusMinutes(5))) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"FUTURE_SAMPLE");
    return service.upload(file,title,deviceId,cameraId,capturedAt);
  }
  @GetMapping({"/api/training/samples/{id}/image","/internal/ai/samples/{id}/image"})
  public ResponseEntity<FileSystemResource> image(@PathVariable UUID id) {
    return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG).cacheControl(CacheControl.noStore())
      .body(new FileSystemResource(service.image(id.toString())));
  }
  @PostMapping("/api/training/samples/{id}/review") public Sample review(@PathVariable UUID id,@Valid @RequestBody Review review,Principal p) {
    return service.review(id.toString(),review,p.getName());
  }
  @PostMapping("/api/training/samples/{id}/retry-label") public ResponseEntity<Job> retryLabel(@PathVariable UUID id) {
    return ResponseEntity.accepted().body(service.retryLabel(id.toString()));
  }
  @GetMapping("/api/training/label-jobs") public List<Job> jobs(@RequestParam(defaultValue="0") @Min(0) @Max(100000) int page,
      @RequestParam(required=false) UUID sampleId) {
    db.expire(); return db.jobs(page*40,sampleId==null?null:sampleId.toString());
  }
  @PostMapping("/internal/ai/jobs/claim") public ResponseEntity<Job> claim() {
    var job=service.claim(); return job==null?ResponseEntity.noContent().build():ResponseEntity.ok(job);
  }
  @PostMapping("/internal/ai/jobs/{id}/heartbeat") public Map<String,Boolean> beat(@PathVariable UUID id,@Valid @RequestBody Lease lease) {
    LabelService.require(db.heartbeat(id.toString(),lease.leaseToken().toString())==1,"LEASE_EXPIRED"); return Map.of("accepted",true);
  }
  @PostMapping("/internal/ai/jobs/{id}/complete") public Map<String,Boolean> complete(@PathVariable UUID id,@Valid @RequestBody Complete body) {
    service.complete(id.toString(),body); return Map.of("accepted",true);
  }
  @PostMapping("/internal/ai/jobs/{id}/fail") public Map<String,Boolean> fail(@PathVariable UUID id,@Valid @RequestBody Failure body) {
    service.fail(id.toString(),body); return Map.of("accepted",true);
  }
}
