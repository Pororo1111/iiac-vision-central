package com.example.central;

import com.example.central.TrainingModels.*;
import static com.example.central.TrainingModels.BASE_MODEL;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

@Service
public class TrainingService {
  final TrainingMapper db;
  final LabelMapper labels;
  final ObjectMapper json;
  public TrainingService(TrainingMapper db,LabelMapper labels,ObjectMapper json) { this.db=db;this.labels=labels;this.json=json; }
  static void require(boolean ok,String reason) { if(!ok) throw new ResponseStatusException(HttpStatus.CONFLICT,reason); }
  @Transactional public void expire() {
    for(var task:db.expired()) {
      db.finish(task.id(),"FAILED","워커 응답 시간 초과");
      db.state(task.runId(),"FAILED",json.writeValueAsString(Map.of("error","워커 응답 시간 초과","failedAction",task.action())));
    }
  }
  @Transactional public Run create(Create request) {
    return create(request.name(),request.sampleIds(),request.confidence(),request.auto(),request.baseModel(),"[]");
  }
  @Transactional public Run create(String name,List<UUID> sampleIds,double confidence,Auto auto) {
    return create(name,sampleIds,confidence,auto,"default","[]");
  }
  /** external은 외부 데이터셋 스냅샷 JSON 배열이다. 외부 데이터셋이 있으면 수집 이미지 없이도 구성할 수 있다. */
  @Transactional public Run create(String name,List<UUID> sampleIds,double confidence,Auto auto,String baseModel,String external) {
    expire();
    baseModel=baseModel==null?"default":baseModel;
    require(baseModel.matches(BASE_MODEL),"INVALID_BASE_MODEL");
    if(baseModel.startsWith("release:")) require(db.released(baseModel.substring(8))==1,"BASE_MODEL_NOT_FOUND");
    require(!sampleIds.isEmpty() || !json.readTree(external).isEmpty(),"TRAINING_DATA_REQUIRED");
    require(new HashSet<>(sampleIds).size()==sampleIds.size(),"DUPLICATE_SAMPLE");
    var samples=new ArrayList<LabelModels.Sample>();
    for(var id:sampleIds.stream().sorted().toList()) {
      var sample=labels.lock(id.toString());
      require(sample!=null && sample.status().equals("LABELED") && sample.proposal()!=null,"LABELED_SAMPLE_REQUIRED");
      samples.add(sample);
    }
    String id=UUID.randomUUID().toString();
    db.insert(id,name,json.writeValueAsString(samples),auto==null?null:json.writeValueAsString(auto),baseModel,external);
    enqueue(id,"PREPARE",Map.of("confidence",confidence));
    return db.run(id);
  }
  void enqueue(String id,String action,Map<String,Object> parameters) {
    try { db.enqueue(UUID.randomUUID().toString(),id,action,json.writeValueAsString(parameters)); }
    catch(DuplicateKeyException e) { throw new ResponseStatusException(HttpStatus.CONFLICT,"TRAINING_BUSY"); }
  }
  @Transactional public Run start(String id,String action,int epochs) {
    expire(); var run=db.lock(id); require(run!=null,"RUN_NOT_FOUND");
    String expected=action.equals("TRAIN")?"DATASET_READY":"TRAINED";
    require(run.state().equals(expected),"RUN_STATE_CONFLICT");
    enqueue(id,action,action.equals("TRAIN")?Map.of("epochs",epochs):Map.of());
    db.state(id,action.equals("TRAIN")?"TRAINING":"EVALUATING","{}"); return db.run(id);
  }
  /**
   * 진행 중인 실행을 중단한다. 작업을 FAILED로 바꾸면 워커의 임대 갱신이 실패하고 워커가 학습 자식 프로세스를 종료한다.
   * 자동 진행 중에도 다음 단계가 등록되지 않는다.
   */
  @Transactional public Run stop(String id) {
    expire(); var run=db.lock(id); require(run!=null,"RUN_NOT_FOUND");
    require(Set.of("PREPARING","DATASET_READY","TRAINING","TRAINED","EVALUATING").contains(run.state()),"RUN_NOT_ACTIVE");
    var task=db.activeTask(id);
    String action=task==null?(run.state().equals("PREPARING")?"PREPARE":run.state().startsWith("EVAL")||run.state().equals("TRAINED")?"EVALUATE":"TRAIN"):task.action();
    if(task!=null) db.finish(task.id(),"FAILED","관리자 요청으로 중단");
    db.state(id,"FAILED",json.writeValueAsString(Map.of("error","관리자 요청으로 중단했습니다.","failedAction",action,"stopped",true)));
    return db.run(id);
  }
  @Transactional public Task claim() { expire(); return db.claim(UUID.randomUUID().toString()); }
  @Transactional public void complete(String id,Result body) {
    var task=db.task(id); require(task!=null,"TASK_NOT_FOUND");
    require(db.heartbeat(id,body.leaseToken().toString())==1,"LEASE_EXPIRED");
    var result=json.readTree(body.summary()); require(result.isObject(),"INVALID_SUMMARY");
    db.finish(id,"COMPLETED",null);
    db.state(task.runId(),switch(task.action()) {case "PREPARE" -> "DATASET_READY";case "TRAIN" -> "TRAINED";default -> "EVALUATED";},body.summary());
    var run=db.run(task.runId());
    if(run.automation()==null) return;
    // 방금 끝난 작업이 유일한 활성 작업이므로 다음 단계를 같은 트랜잭션에서 바로 등록한다.
    if(task.action().equals("PREPARE")) start(run.id(),"TRAIN",json.readTree(run.automation()).path("epochs").asInt(1));
    else if(task.action().equals("TRAIN")) start(run.id(),"EVALUATE",0);
  }
  @Transactional public void fail(String id,LabelModels.Failure body) {
    var task=db.task(id); require(task!=null,"TASK_NOT_FOUND");
    require(db.heartbeat(id,body.leaseToken().toString())==1,"LEASE_EXPIRED");
    db.finish(id,"FAILED",body.error());
    db.state(task.runId(),"FAILED",json.writeValueAsString(Map.of("error",body.error(),"failedAction",task.action())));
  }
}
