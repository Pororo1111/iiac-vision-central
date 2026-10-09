package com.example.central;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.*;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController
public class DeploymentController {
  record Create(@NotNull UUID modelId,@NotBlank @Size(max=64) String deviceId) {}
  record PolicyChange(@NotBlank @Pattern(regexp="AUTO|PINNED") String mode,UUID modelId,@Size(max=200) String note) {}
  public record Report(@NotBlank @Pattern(regexp="DOWNLOADING|VALIDATING|APPLYING|APPLIED|FAILED|ROLLED_BACK") String status,
      @Pattern(regexp="[0-9a-f]{64}") String activeSha256,@Size(max=500) String message) {}
  final DeploymentService service;
  final DeploymentMapper db;
  public DeploymentController(DeploymentService service,DeploymentMapper db){this.service=service;this.db=db;}
  @GetMapping("/api/training/models") public List<DeploymentMapper.Catalog> models(){return db.catalog();}
  @PostMapping("/api/training/models/base") public DeploymentMapper.Model registerBase() throws Exception{return service.registerBase();}
  @GetMapping("/api/training/device-policies") public List<Map<String,Object>> policies(){return service.board();}
  @PostMapping("/api/training/device-policies/{deviceId}") public Map<String,Object> policy(@PathVariable @Size(max=64) String deviceId,@Valid @RequestBody PolicyChange body,Principal principal) throws Exception {
    return service.setPolicy(deviceId,body.mode(),body.modelId()==null?null:body.modelId().toString(),body.note()==null?"":body.note().strip(),principal.getName());
  }
  @GetMapping("/api/training/device-policies/{deviceId}/history") public List<DeploymentMapper.PolicyHistory> policyHistory(@PathVariable @Size(max=64) String deviceId){return db.history(deviceId);}
  /** override=true: 평가 기준 미달 실행도 관리자 판단으로 후보 등록한다. */
  @PostMapping("/api/training/runs/{id}/release") public DeploymentMapper.Model release(@PathVariable UUID id,@RequestParam(defaultValue="false") boolean override,Principal principal) throws Exception {
    return service.register(id.toString(),override?principal.getName():null);
  }
  @GetMapping("/api/training/deployments") public List<DeploymentMapper.Deployment> deployments(){return db.deployments();}
  @PostMapping("/api/training/deployments") public ResponseEntity<DeploymentMapper.Deployment> create(@Valid @RequestBody Create body) throws Exception{return ResponseEntity.accepted().body(service.create(body.modelId().toString(),body.deviceId(),DeploymentService.Source.MANUAL));}
  @PostMapping("/api/training/deployments/{id}/cancel") public DeploymentMapper.Deployment cancel(@PathVariable UUID id,Principal principal){return service.cancel(id.toString(),principal.getName());}
  @GetMapping("/api/iot/deployments/desired") public ResponseEntity<?> desired(Principal principal) {
    var desired=service.desired(principal.getName());
    return desired==null?ResponseEntity.noContent().build():ResponseEntity.ok(desired);
  }
  @GetMapping("/api/iot/deployments/{id}/model") public ResponseEntity<FileSystemResource> model(@PathVariable UUID id,Principal principal) throws Exception {
    var deployment=service.owned(id.toString(),principal.getName());
    TrainingService.require(Set.of("PENDING","DOWNLOADING","VALIDATING","APPLYING").contains(deployment.status()),"DEPLOYMENT_FINISHED");
    var model=db.model(deployment.modelId());var file=service.file(model.id());
    TrainingService.require(service.cachedHash(file).equals(model.sha256()),"MODEL_HASH_MISMATCH");
    return ResponseEntity.ok().contentType(MediaType.APPLICATION_OCTET_STREAM).contentLength(model.sizeBytes()).cacheControl(CacheControl.noStore()).body(new FileSystemResource(file));
  }
  @PostMapping("/api/iot/deployments/{id}/status") public Map<String,Boolean> report(@PathVariable UUID id,Principal principal,@Valid @RequestBody Report body) {service.report(id.toString(),principal.getName(),body);return Map.of("accepted",true);}
}
