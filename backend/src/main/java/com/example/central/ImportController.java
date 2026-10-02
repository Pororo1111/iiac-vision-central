package com.example.central;

import com.example.central.LabelModels.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.*;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

@RestController
public class ImportController {
  final ImportService service;
  public ImportController(ImportService service) { this.service=service; }

  @PostMapping(value="/api/training/uploads",consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
  public List<Sample> images(@RequestParam("files") List<MultipartFile> files) throws Exception {
    return service.uploadImages(files);
  }
  @PostMapping(value="/api/training/imports/video",consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
  public ResponseEntity<Import> video(@RequestParam MultipartFile file,@RequestParam(required=false) @Size(max=160) String title,
      @RequestParam(defaultValue="2") double intervalSeconds,@RequestParam(defaultValue="60") int maxFrames,Principal p) throws Exception {
    return ResponseEntity.accepted().body(service.createVideo(file,title,intervalSeconds,maxFrames,p.getName()));
  }
  @PostMapping("/api/training/imports/youtube")
  public ResponseEntity<Import> youtube(@Valid @RequestBody YoutubeImport body,Principal p) {
    return ResponseEntity.accepted().body(service.createYoutube(body,p.getName()));
  }
  @GetMapping("/api/training/imports") public List<Import> imports() { return service.recent(); }

  @PostMapping("/internal/ai/imports/claim") public ResponseEntity<Import> claim() {
    var job=service.claim(); return job==null?ResponseEntity.noContent().build():ResponseEntity.ok(job);
  }
  @PostMapping("/internal/ai/imports/{id}/heartbeat") public Map<String,Boolean> beat(@PathVariable UUID id,@Valid @RequestBody Lease lease) {
    service.heartbeat(id.toString(),lease.leaseToken().toString()); return Map.of("accepted",true);
  }
  @GetMapping("/internal/ai/imports/{id}/video") public ResponseEntity<FileSystemResource> file(@PathVariable UUID id) {
    return ResponseEntity.ok().contentType(MediaType.APPLICATION_OCTET_STREAM).body(new FileSystemResource(service.videoFile(id.toString())));
  }
  @PostMapping(value="/internal/ai/imports/{id}/frames",consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
  public Sample frame(@PathVariable UUID id,@RequestParam UUID leaseToken,@RequestParam double positionSeconds,
      @RequestParam MultipartFile file) throws Exception {
    return service.frame(id.toString(),leaseToken.toString(),positionSeconds,file);
  }
  @PostMapping("/internal/ai/imports/{id}/complete") public Map<String,Boolean> complete(@PathVariable UUID id,@Valid @RequestBody Lease lease) throws Exception {
    service.complete(id.toString(),lease.leaseToken().toString()); return Map.of("accepted",true);
  }
  @PostMapping("/internal/ai/imports/{id}/fail") public Map<String,Boolean> fail(@PathVariable UUID id,@Valid @RequestBody Failure body) throws Exception {
    service.fail(id.toString(),body.leaseToken().toString(),body.error()); return Map.of("accepted",true);
  }
}
