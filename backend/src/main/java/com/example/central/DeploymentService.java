package com.example.central;

import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

@Service
public class DeploymentService {
  final DeploymentMapper db;
  final TrainingMapper training;
  final CentralMapper devices;
  final ObjectMapper json;
  final Path root;
  final Path baseModel;
  public DeploymentService(DeploymentMapper db,TrainingMapper training,CentralMapper devices,ObjectMapper json,@Value("${app.training-runs-root:}") String root,
      @Value("${app.base-model-path:}") String baseModel) {
    this.db=db;this.training=training;this.devices=devices;this.json=json;this.root=root.isBlank()?null:Path.of(root).toAbsolutePath().normalize();
    this.baseModel=baseModel.isBlank()?null:Path.of(baseModel).toAbsolutePath().normalize();
  }
  /**
   * 설정한 현장 기본 모델 파일을 배포 후보(BASE)로 등록한다. 해시가 장치 보고값이나 평가 기준 모델과 같을 때만 허용하며,
   * 파일은 다른 후보와 같은 위치(root/id/release/model.pt)에 복사한다. 이미 등록된 해시면 기존 후보를 돌려준다.
   */
  public DeploymentMapper.Model registerBase() throws Exception {
    TrainingService.require(root!=null && baseModel!=null,"BASE_MODEL_UNCONFIGURED");
    TrainingService.require(Files.isRegularFile(baseModel),"BASE_MODEL_MISSING");
    long size=Files.size(baseModel);TrainingService.require(size>0 && size<=536870912L,"MODEL_TOO_LARGE");
    String sha=hash(baseModel);
    var existing=db.baseBySha(sha);if(existing!=null) return existing;
    TrainingService.require(db.knownFieldHash(sha),"BASE_HASH_UNVERIFIED");
    String id=UUID.randomUUID().toString();Path folder=root.resolve(id);Path release=folder.resolve("release");
    try {
      Files.createDirectories(release);Path temp=release.resolve("model.pt.tmp");
      Files.copy(baseModel,temp);
      TrainingService.require(hash(temp).equals(sha),"MODEL_HASH_MISMATCH");
      Files.move(temp,release.resolve("model.pt"),StandardCopyOption.ATOMIC_MOVE);
      Path parent=baseModel.getParent()==null?null:baseModel.getParent().getParent();
      String label=parent!=null && baseModel.getParent().getFileName().toString().equals("weights")?parent.getFileName().toString():baseModel.getFileName().toString();
      db.registerBase(id,sha,size,label.length()>100?label.substring(0,100):label);
      return db.model(id);
    } catch(Exception e) {
      try(var files=Files.walk(folder)) {files.sorted(Comparator.reverseOrder()).forEach(p->p.toFile().delete());} catch(IOException ignored) {}
      if(e instanceof DuplicateKeyException) return db.baseBySha(sha);
      throw e;
    }
  }
  Path file(String id) throws IOException {
    TrainingService.require(root!=null,"MODEL_STORAGE_UNAVAILABLE");
    Path file=root.resolve(UUID.fromString(id).toString()).resolve("release/model.pt");
    TrainingService.require(Files.isRegularFile(file) && file.toRealPath().startsWith(root.toRealPath()),"MODEL_FILE_MISSING");
    return file;
  }
  record HashKey(Path path,long size,long modified) {}
  final Map<HashKey,String> hashes=new java.util.concurrent.ConcurrentHashMap<>();
  /** 같은 경로·크기·수정 시각이면 이전 해시를 재사용해 다운로드마다 파일 전체를 다시 읽지 않는다. */
  String cachedHash(Path path) throws Exception {
    var key=new HashKey(path,Files.size(path),Files.getLastModifiedTime(path).toMillis());
    String known=hashes.get(key);if(known!=null) return known;
    String value=hash(path);hashes.put(key,value);return value;
  }
  /** IoT가 가져갈 미완료 배포. heartbeat 응답과 desired 조회가 같은 형식을 쓴다. */
  Map<String,Object> desired(String device) {
    var deployment=db.desired(device);if(deployment==null) return null;
    var model=db.model(deployment.modelId());
    String name=db.modelName(model.id());
    // IoT가 적용 후 heartbeat modelName으로 보고할 표시 이름. 이름이 없으면 modelId를 쓴다.
    return Map.ofEntries(Map.entry("id",deployment.id()),Map.entry("modelId",model.id()),Map.entry("modelName",name==null||name.isBlank()?model.id():name),
        Map.entry("deviceId",deployment.deviceId()),Map.entry("status",deployment.status()),Map.entry("sha256",model.sha256()),Map.entry("sizeBytes",model.sizeBytes()),
        Map.entry("format","pt"),Map.entry("classes",classes(model.id())),Map.entry("downloadPath","/api/iot/deployments/"+deployment.id()+"/model"));
  }
  /** 배포 정보에 보낼 후보 모델의 클래스 표. IoT는 이 표와 내려받은 모델의 클래스가 같은지 확인한다. */
  List<String> classes(String model) {
    try {return List.of(json.readValue(db.classes(model),String[].class));}
    catch(Exception e) {throw new IllegalStateException("MODEL_CLASSES_INVALID",e);}
  }
  /** 학습 결과 model.json의 classes(6클래스 등). 기록이 없는 이전 실행은 null이며 DB 기본값(4클래스)을 쓴다. */
  String runClasses(String id) throws Exception {
    if(root==null) return null;
    Path meta=root.resolve(UUID.fromString(id).toString()).resolve("model.json");
    if(!Files.isRegularFile(meta)) return null;
    var node=json.readTree(Files.readString(meta)).path("classes");
    if(!node.isArray()) return null;
    TrainingService.require(node.size()>=1 && node.size()<=32,"MODEL_CLASSES_INVALID");
    for(var c:node) TrainingService.require(c.isTextual() && c.asText().matches("[a-z_]{1,32}"),"MODEL_CLASSES_INVALID");
    return json.writeValueAsString(node);
  }
  static String hash(Path path) throws Exception {
    var digest=MessageDigest.getInstance("SHA-256");
    try(var input=Files.newInputStream(path)) {byte[] bytes=new byte[65536];int n;while((n=input.read(bytes))!=-1) digest.update(bytes,0,n);}
    return HexFormat.of().formatHex(digest.digest());
  }
  @Transactional public DeploymentMapper.Model register(String id) throws Exception {return register(id,null);}
  /**
   * 평가를 마친 실행을 배포 후보로 등록한다. overrideBy(관리자)가 있으면 평가 기준 미달이어도 등록하고 summary.releaseOverride에 남긴다.
   * 자동 경로는 overrideBy 없이 호출하므로 기준 미달이면 그대로 멈춘다.
   */
  @Transactional public DeploymentMapper.Model register(String id,String overrideBy) throws Exception {
    var run=training.lock(id);
    TrainingService.require(run!=null && run.state().equals("EVALUATED"),"EVALUATED_MODEL_REQUIRED");
    var summary=json.readTree(run.summary());
    boolean eligible=summary.path("evaluation").path("eligibleForPromotion").asBoolean(false);
    TrainingService.require(eligible || overrideBy!=null,"EVALUATION_REJECTED");
    if(!eligible) copyCandidate(id,summary.path("modelSha256").asText());
    Path file=file(id); long size=Files.size(file);String hash=hash(file);
    TrainingService.require(size>0 && size<=536870912L && hash.equals(summary.path("modelSha256").asText()),"MODEL_HASH_MISMATCH");
    if(!eligible && training.released(id)==0)
      training.note(id,json.writeValueAsString(Map.of("releaseOverride",Map.of("by",overrideBy,"at",java.time.OffsetDateTime.now(java.time.ZoneOffset.UTC).toString()))));
    db.register(id,hash,size);String classes=runClasses(id);if(classes!=null) db.setClasses(id,classes);var model=db.model(id);
    TrainingService.require(model.sha256().equals(hash) && model.sizeBytes()==size,"MODEL_VERSION_IMMUTABLE");return model;
  }
  /** 워커는 평가 통과 실행만 release/model.pt로 복사하므로, 관리자 허용 등록 때 학습 결과(model.json weights)를 해시 확인 후 복사한다. */
  void copyCandidate(String id,String sha) throws Exception {
    TrainingService.require(root!=null,"MODEL_STORAGE_UNAVAILABLE");
    Path folder=root.resolve(UUID.fromString(id).toString());Path target=folder.resolve("release/model.pt");
    if(Files.isRegularFile(target)) return;
    Path meta=folder.resolve("model.json");TrainingService.require(Files.isRegularFile(meta),"MODEL_FILE_MISSING");
    Path weights=Path.of(json.readTree(Files.readString(meta)).path("weights").asText(""));
    TrainingService.require(Files.isRegularFile(weights) && weights.toRealPath().startsWith(folder.toRealPath()),"MODEL_FILE_MISSING");
    TrainingService.require(hash(weights).equals(sha),"MODEL_HASH_MISMATCH");
    Files.createDirectories(target.getParent());Path temp=target.resolveSibling("model.pt.tmp");
    Files.copy(weights,temp,StandardCopyOption.REPLACE_EXISTING);
    TrainingService.require(hash(temp).equals(sha),"MODEL_HASH_MISMATCH");
    Files.move(temp,target,StandardCopyOption.ATOMIC_MOVE);
  }
  /** 배포 요청 경로. MANUAL은 관리자 요청, AUTO는 자동 파이프라인이다. */
  enum Source {MANUAL,AUTO}
  /**
   * 장치 정책을 따른다. AUTO 정책은 모두 허용한다. PINNED 정책은 자동 배포를 막고, 관리자 요청은 고정 모델의 재배포만 허용한다.
   * 고정 모델 자체를 바꾸려면 {@link #setPolicy}를 사용한다.
   */
  @Transactional public DeploymentMapper.Deployment create(String model,String device,Source source) throws Exception {
    var release=db.model(model);TrainingService.require(release!=null,"MODEL_NOT_FOUND");
    var policy=db.lockPolicy(device);TrainingService.require(policy!=null,"DEVICE_NOT_REGISTERED");
    if(policy.mode().equals("PINNED")) TrainingService.require(source==Source.MANUAL && model.equals(policy.pinnedModelId()),"DEVICE_PINNED");
    return insert(release,device);
  }
  private DeploymentMapper.Deployment insert(DeploymentMapper.Model release,String device) throws Exception {
    TrainingService.require(cachedHash(file(release.id())).equals(release.sha256()),"MODEL_HASH_MISMATCH");
    String id=UUID.randomUUID().toString();
    try {db.insert(id,release.id(),device);}catch(DuplicateKeyException e){throw new ResponseStatusException(HttpStatus.CONFLICT,"DEPLOYMENT_ACTIVE");}
    return db.deployment(id);
  }
  /**
   * 장치 배포 정책을 바꾼다. PINNED로 바꾸면 고정 모델이 아닌 적용 전 배포를 취소하고(적용 중이면 409),
   * 고정 모델이 현재 활성 모델과 다르면 배포를 요청한다. AUTO로 바꾸면 진행 중인 배포는 그대로 둔다.
   */
  @Transactional public Map<String,Object> setPolicy(String device,String mode,String model,String note,String admin) throws Exception {
    var policy=db.lockPolicy(device);
    if(policy==null) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"DEVICE_NOT_FOUND");
    if(mode.equals("AUTO")) model=null;
    DeploymentMapper.Model release=null;
    if(model!=null) {release=db.model(model);TrainingService.require(release!=null,"MODEL_NOT_FOUND");}
    var active=db.desired(device);
    String outcome="NONE",deploymentId=null;
    if(active!=null && mode.equals("PINNED")) {
      if(active.modelId().equals(model)) {outcome="IN_PROGRESS";deploymentId=active.id();}
      else {
        TrainingService.require(!active.status().equals("APPLYING"),"DEPLOYMENT_APPLYING");
        db.update(active.id(),"CANCELED",active.activeSha256(),"배포 정책 변경으로 취소 ("+admin+", "+active.status()+" 단계)");
        active=null;
      }
    }
    db.savePolicy(device,mode,model,note,admin);db.policyHistory(device,mode,model,note,admin);
    if(release!=null && active==null) {
      if(release.sha256().equals(policy.activeSha256())) outcome="ALREADY_ACTIVE";
      else {deploymentId=insert(release,device).id();outcome="REQUESTED";}
    }
    var result=new LinkedHashMap<String,Object>();
    result.put("deviceId",device);result.put("mode",mode);result.put("pinnedModelId",model);
    result.put("deployment",outcome);result.put("deploymentId",deploymentId);
    return result;
  }
  /** 장치별 정책·현재 모델·최근 배포. 고정 모델이 있고 보고된 활성 해시와 다르면 drift=true다. */
  List<Map<String,Object>> board() {
    var latest=new HashMap<String,DeploymentMapper.Deployment>();
    for(var d:db.latestPerDevice()) latest.put(d.deviceId(),d);
    var rows=new ArrayList<Map<String,Object>>();
    for(var p:db.policies()) {
      var row=new LinkedHashMap<String,Object>();
      row.put("policy",p);row.put("latestDeployment",latest.get(p.deviceId()));
      row.put("drift",p.pinnedSha256()!=null && p.activeSha256()!=null && !p.pinnedSha256().equals(p.activeSha256()));
      rows.add(row);
    }
    return rows;
  }
  DeploymentMapper.Deployment owned(String id,String device) {
    var deployment=db.deployment(id);
    if(deployment==null || !deployment.deviceId().equals(device)) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"DEPLOYMENT_NOT_FOUND");
    return deployment;
  }
  @Transactional public DeploymentMapper.Deployment cancel(String id,String admin) {
    var deployment=db.lock(id);
    if(deployment==null) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"DEPLOYMENT_NOT_FOUND");
    if(deployment.status().equals("CANCELED")) return deployment;
    TrainingService.require(Set.of("PENDING","DOWNLOADING","VALIDATING").contains(deployment.status()),"DEPLOYMENT_NOT_CANCELABLE");
    db.update(id,"CANCELED",deployment.activeSha256(),"관리자 취소 ("+admin+", "+deployment.status()+" 단계)");
    return db.deployment(id);
  }
  @Transactional public void report(String id,String device,DeploymentController.Report report) {
    owned(id,device);var deployment=db.lock(id);
    if(deployment.status().equals(report.status())) {
      TrainingService.require(Objects.equals(deployment.activeSha256(),report.activeSha256()),"REPORT_CONFLICT");return;
    }
    String current=deployment.status(),next=report.status();
    var transitions=Map.of("PENDING","DOWNLOADING","DOWNLOADING","VALIDATING","VALIDATING","APPLYING","APPLYING","APPLIED");
    boolean failed=next.equals("FAILED") && transitions.containsKey(current);
    boolean rollback=next.equals("ROLLED_BACK") && current.equals("APPLYING");
    TrainingService.require(failed || rollback || next.equals(transitions.get(current)),"DEPLOYMENT_STATE_CONFLICT");
    if(next.equals("APPLIED")) TrainingService.require(db.model(deployment.modelId()).sha256().equals(report.activeSha256()),"ACTIVE_HASH_MISMATCH");
    db.update(id,next,report.activeSha256(),report.message()==null?"":report.message());
  }
}
