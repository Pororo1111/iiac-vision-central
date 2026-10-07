package com.example.central;

import com.example.central.PipelineModels.*;
import jakarta.validation.Valid;
import java.security.Principal;
import java.util.*;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
public class PipelineController {
  final PipelineService service;
  public PipelineController(PipelineService service) { this.service=service; }

  @GetMapping("/api/training/pipelines") public List<View> pipelines() { return service.list(); }
  @PostMapping("/api/training/pipelines") public ResponseEntity<View> create(@Valid @RequestBody Create body,Principal p) {
    return ResponseEntity.accepted().body(service.create(body,p.getName()));
  }
  @PostMapping("/api/training/pipelines/{id}/cancel") public Map<String,Boolean> cancel(@PathVariable UUID id) {
    service.cancel(id.toString()); return Map.of("cancelled",true);
  }
  @DeleteMapping("/api/training/pipelines/{id}") public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.delete(id.toString()); return ResponseEntity.noContent().build();
  }
  @GetMapping("/api/training/auto-pipeline") public AutoStatus auto() { return service.autoStatus(); }
  @PutMapping("/api/training/auto-pipeline") public AutoStatus updateAuto(@Valid @RequestBody AutoUpdate body) { return service.updateAuto(body); }
  @PostMapping("/api/training/auto-pipeline/run") public ResponseEntity<View> runAuto() { return ResponseEntity.accepted().body(service.runAutoNow()); }
  @GetMapping("/api/training/base-models") public List<TrainingModels.BaseModel> baseModels() { return service.baseModels(); }
  @GetMapping("/api/training/external-datasets") public List<External> externals() { return service.externals(); }
  @GetMapping("/api/training/external-datasets/lineage") public Map<String,List<String>> lineage(@RequestParam(required=false) String baseModel) {
    return Map.of("inBase",service.lineageExternal(baseModel));
  }
  @PostMapping("/api/training/external-datasets") public ResponseEntity<External> requestExternal(@Valid @RequestBody ExternalRequest body) {
    return ResponseEntity.accepted().body(service.requestExternal(body.url()));
  }

  @PostMapping("/internal/ai/pipelines/naming/claim") public ResponseEntity<NamingJob> claimNaming() {
    var job=service.claimNaming(); return job==null?ResponseEntity.noContent().build():ResponseEntity.ok(job);
  }
  @PostMapping("/internal/ai/pipelines/{id}/name") public Map<String,Boolean> name(@PathVariable UUID id,@Valid @RequestBody NameResult body) {
    service.name(id.toString(),body.name()); return Map.of("accepted",true);
  }
  @DeleteMapping("/api/training/external-datasets/{id}") public ResponseEntity<Void> deleteExternal(@PathVariable UUID id) {
    service.deleteExternal(id.toString()); return ResponseEntity.noContent().build();
  }
  @PostMapping("/internal/ai/external/claim") public ResponseEntity<External> claim() {
    var job=service.claimExternal(); return job==null?ResponseEntity.noContent().build():ResponseEntity.ok(job);
  }
  @PostMapping("/internal/ai/external/{id}/heartbeat") public Map<String,Boolean> beat(@PathVariable UUID id,@Valid @RequestBody ExternalBeat body) {
    service.progressExternal(id.toString(),body); return Map.of("accepted",true);
  }
  @PostMapping("/internal/ai/external/{id}/complete") public Map<String,Boolean> complete(@PathVariable UUID id,@Valid @RequestBody ExternalComplete body) {
    service.completeExternal(id.toString(),body); return Map.of("accepted",true);
  }
  @PostMapping("/internal/ai/external/{id}/fail") public Map<String,Boolean> fail(@PathVariable UUID id,@Valid @RequestBody LabelModels.Failure body) {
    service.failExternal(id.toString(),body.leaseToken().toString(),body.error()); return Map.of("accepted",true);
  }
}
