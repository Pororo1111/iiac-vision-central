package com.example.central;

import com.example.central.TrainingModels.*;
import java.util.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

/**
 * 자동 실행의 평가 이후 단계(후보 등록·배포 요청)를 담당한다. 주기 트리거는 PipelineService의 전역 자동 파이프라인이 맡는다.
 * 등록·배포는 각자 트랜잭션으로 실행해 실패해도 완료된 평가 결과를 되돌리지 않는다.
 * 평가 기준 미달이면 등록하지 않고 멈추며, 현장 적용은 IoT의 명시적 CLI가 수행한다.
 */
@Component
@EnableScheduling
public class PipelineAutomation {
  static final Logger log=LoggerFactory.getLogger(PipelineAutomation.class);
  final TrainingService training;
  final TrainingMapper db;
  final DeploymentService deployments;
  final ObjectMapper json;
  public PipelineAutomation(TrainingService training,TrainingMapper db,DeploymentService deployments,ObjectMapper json) {
    this.training=training;this.db=db;this.deployments=deployments;this.json=json;
  }

  public void afterEvaluate(String runId) {
    var run=db.run(runId);
    if(run==null || run.automation()==null || !run.state().equals("EVALUATED")) return;
    var outcome=new LinkedHashMap<String,Object>();
    try {
      var model=deployments.register(runId);
      outcome.put("release","REGISTERED");
      String device=json.readTree(run.automation()).path("deviceId").asText("");
      if(device.isBlank()) outcome.put("deployment","SKIPPED_NO_DEVICE");
      else {
        var deployment=deployments.create(model.id(),device,DeploymentService.Source.AUTO);
        outcome.put("deployment","REQUESTED");outcome.put("deploymentId",deployment.id());
      }
    } catch(ResponseStatusException e) {
      outcome.put(outcome.containsKey("release")?"deployment":"release","STOPPED");
      outcome.put("reason",e.getReason());
    } catch(Exception e) {
      log.warn("자동 실행 후속 단계 실패: run={}",runId,e);
      outcome.put("reason","AUTOMATION_ERROR");
    }
    db.note(runId,json.writeValueAsString(Map.of("automation",outcome)));
  }
}
