package com.example.central;

import com.example.central.TrainingModels.*;
import jakarta.validation.Valid;
import java.util.*;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
public class TrainingController {
  final TrainingService service;
  final TrainingMapper db;
  final PipelineAutomation automation;
  public TrainingController(TrainingService service,TrainingMapper db,PipelineAutomation automation) {this.service=service;this.db=db;this.automation=automation;}
  @GetMapping("/api/training/runs") public List<RunView> runs() {service.expire();return db.runs().stream().map(RunView::of).toList();}
  @PostMapping("/api/training/runs") public ResponseEntity<RunView> create(@Valid @RequestBody Create body) {return ResponseEntity.accepted().body(RunView.of(service.create(body)));}
  @GetMapping("/internal/ai/training/runs/{id}") public Run run(@PathVariable UUID id) {
    var run=db.run(id.toString());TrainingService.require(run!=null,"RUN_NOT_FOUND");return run;
  }
  @GetMapping("/api/training/runs/{id}") public RunView view(@PathVariable UUID id) {return RunView.of(run(id));}
  @PostMapping("/api/training/runs/{id}/train") public RunView train(@PathVariable UUID id,@Valid @RequestBody Train body) {return RunView.of(service.start(id.toString(),"TRAIN",body.epochs()));}
  @PostMapping("/api/training/runs/{id}/stop") public RunView stop(@PathVariable UUID id) {return RunView.of(service.stop(id.toString()));}
  @PostMapping("/api/training/runs/{id}/evaluate") public RunView evaluate(@PathVariable UUID id) {return RunView.of(service.start(id.toString(),"EVALUATE",0));}
  @PostMapping("/internal/ai/training/tasks/claim") public ResponseEntity<Task> claim() {var task=service.claim();return task==null?ResponseEntity.noContent().build():ResponseEntity.ok(task);}
  @PostMapping("/internal/ai/training/tasks/{id}/heartbeat") public Map<String,Boolean> heartbeat(@PathVariable UUID id,@Valid @RequestBody LabelModels.Lease body) {TrainingService.require(db.heartbeat(id.toString(),body.leaseToken().toString())==1,"LEASE_EXPIRED");return Map.of("accepted",true);}
  @PostMapping("/internal/ai/training/tasks/{id}/complete") public Map<String,Boolean> complete(@PathVariable UUID id,@Valid @RequestBody Result body) {
    service.complete(id.toString(),body);
    // 평가 결과가 커밋된 뒤 별도 트랜잭션으로 후보 등록·배포 요청을 시도한다.
    var task=db.taskView(id.toString());
    if(task!=null && task.action().equals("EVALUATE")) automation.afterEvaluate(task.runId());
    return Map.of("accepted",true);
  }
  @PostMapping("/internal/ai/training/tasks/{id}/fail") public Map<String,Boolean> fail(@PathVariable UUID id,@Valid @RequestBody LabelModels.Failure body) {service.fail(id.toString(),body);return Map.of("accepted",true);}
}
