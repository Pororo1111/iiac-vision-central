package com.example.central;

import com.example.central.PipelineModels.*;
import com.example.central.TrainingModels.Auto;
import com.example.central.TrainingModels.BaseModel;
import com.example.central.TrainingModels.RunView;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

/**
 * 마법사로 만든 파이프라인을 수집 → 자동 라벨링 → 데이터셋 구성(학습 실행 생성)까지 진행한다.
 * 이후 학습·평가·후보 등록은 기존 training_runs 자동 진행(automation)이 담당하며, 배포는 관리자가 목록에서 요청한다.
 * Roboflow 외부 데이터셋 요청·워커 작업 상태도 함께 관리한다.
 */
@Service
public class PipelineService {
  static final Logger log=LoggerFactory.getLogger(PipelineService.class);
  static final Pattern SLUG=Pattern.compile("[A-Za-z0-9_-]{1,100}");
  /** 기준 데이터셋(ppe4-rf27-combined)에 이미 전부 들어 있어 따로 불러올 필요가 없는 Roboflow 데이터셋. */
  static final Set<String> BASELINE_SOURCES=Set.of("roboflow-universe-projects/construction-site-safety/27");
  static boolean inBaseline(String workspace,String project,Integer version) {
    return version!=null && BASELINE_SOURCES.contains(workspace.toLowerCase(Locale.ROOT)+"/"+project.toLowerCase(Locale.ROOT)+"/"+version);
  }
  final PipelineMapper db;
  final TrainingService training;
  final TrainingMapper runs;
  final DeploymentMapper deployments;
  final CentralMapper devices;
  final LabelMapper labelMapper;
  final ObjectMapper json;
  final TransactionTemplate tx;
  final java.nio.file.Path runsRoot;
  public PipelineService(PipelineMapper db,TrainingService training,TrainingMapper runs,DeploymentMapper deployments,
      CentralMapper devices,LabelMapper labelMapper,ObjectMapper json,PlatformTransactionManager manager,
      @org.springframework.beans.factory.annotation.Value("${app.training-runs-root:}") String root) {
    this.db=db;this.training=training;this.runs=runs;this.deployments=deployments;this.devices=devices;this.labelMapper=labelMapper;this.json=json;
    this.tx=new TransactionTemplate(manager);
    this.runsRoot=root.isBlank()?null:java.nio.file.Path.of(root).normalize();
  }
  static ResponseStatusException bad(String code) { return new ResponseStatusException(HttpStatus.BAD_REQUEST,code); }
  static void require(boolean ok,String code) { TrainingService.require(ok,code); }

  // ---- 베이스 모델 ----
  public List<BaseModel> baseModels() {
    var list=new ArrayList<BaseModel>(List.of(
      new BaseModel("default","현장 기본 모델","PPE 4클래스로 학습된 현재 기준 모델에서 이어학습합니다. 처음이라면 이것을 고르세요.","SITE","중형(m) · 44MB",null,null),
      new BaseModel("yolo26n","YOLO26n 사전학습","COCO 사전학습 가중치로 처음부터 학습합니다. 가볍고 빠르지만 정확도는 낮을 수 있습니다.","PRETRAINED","소형(n) · 6MB",null,null),
      new BaseModel("yolo26m","YOLO26m 사전학습","COCO 사전학습 가중치로 처음부터 학습합니다. 현장 기본 모델과 같은 크기라 느립니다.","PRETRAINED","중형(m) · 44MB",null,null)));
    for(var release:runs.releases()) {
      // 파일 크기로 규모를 구분한다(n 약 6MB, m 약 44MB).
      String size=(release.sizeBytes()<20_000_000?"소형(n) · ":"중형(m) · ")+Math.round(release.sizeBytes()/1_000_000.0)+"MB";
      list.add(new BaseModel("release:"+release.id(),release.name(),"이전에 평가를 통과해 등록된 후보에서 이어학습합니다.","RELEASE",size,release.map(),release.createdAt()));
    }
    return list;
  }

  // ---- Roboflow 외부 데이터셋 ----
  /** universe.roboflow.com/{workspace}/{project}[/dataset/{version}] 만 허용한다. */
  public External requestExternal(String url) {
    String raw=url.strip();
    if(!raw.contains("://")) raw="https://"+raw;
    URI uri;
    try { uri=URI.create(raw); } catch(IllegalArgumentException e) { throw bad("INVALID_ROBOFLOW_URL"); }
    if(!"https".equalsIgnoreCase(uri.getScheme()) && !"http".equalsIgnoreCase(uri.getScheme())) throw bad("INVALID_ROBOFLOW_URL");
    if(!"universe.roboflow.com".equalsIgnoreCase(uri.getHost())) throw bad("INVALID_ROBOFLOW_URL");
    var parts=Arrays.stream(Objects.requireNonNullElse(uri.getRawPath(),"").split("/")).filter(p -> !p.isEmpty())
      .map(p -> URLDecoder.decode(p,StandardCharsets.UTF_8)).toList();
    if(parts.size()<2 || !SLUG.matcher(parts.get(0)).matches() || !SLUG.matcher(parts.get(1)).matches()) throw bad("INVALID_ROBOFLOW_URL");
    Integer version=null;
    if(parts.size()>=4 && parts.get(2).equalsIgnoreCase("dataset")) {
      try { version=Integer.parseInt(parts.get(3)); } catch(NumberFormatException e) { throw bad("INVALID_ROBOFLOW_URL"); }
      if(version<=0 || version>100000) throw bad("INVALID_ROBOFLOW_URL");
    }
    String workspace=parts.get(0),project=parts.get(1);
    if(inBaseline(workspace,project,version)) throw new ResponseStatusException(HttpStatus.CONFLICT,"ALREADY_IN_BASELINE");
    var same=db.sameExternal(workspace,project,version);
    if(same!=null) return same;
    String canonical="https://universe.roboflow.com/"+workspace+"/"+project+(version==null?"":"/dataset/"+version);
    String id=UUID.randomUUID().toString();
    db.insertExternal(id,canonical,workspace,project,version);
    return db.external(id);
  }
  public List<External> externals() {
    db.expireExternal();
    return db.externals().stream().filter(e -> !inBaseline(e.workspace(),e.project(),e.version())).toList();
  }
  public External claimExternal() { db.expireExternal(); return db.claimExternal(UUID.randomUUID().toString()); }
  public void heartbeatExternal(String id,String lease) { require(db.heartbeatExternal(id,lease)==1,"LEASE_EXPIRED"); }
  public void progressExternal(String id,ExternalBeat beat) {
    require(db.progressExternal(id,beat.leaseToken().toString(),beat.phase(),beat.progress())==1,"LEASE_EXPIRED");
  }
  /** 실패한 불러오기 기록만 지운다. 이미 받은 파일(pipeline 캐시)은 건드리지 않는다. */
  public void deleteExternal(String id) {
    var external=db.external(id);
    if(external==null) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"EXTERNAL_NOT_FOUND");
    require(db.deleteFailedExternal(id)==1,"ONLY_FAILED_DELETABLE");
  }
  public void completeExternal(String id,ExternalComplete body) {
    heartbeatExternal(id,body.leaseToken().toString());
    require(body.classes().size()==body.mapping().size(),"MAPPING_SIZE_MISMATCH");
    require(body.mapping().stream().anyMatch(Objects::nonNull),"NO_MAPPED_CLASS");
    require(json.readTree(body.counts()).isObject(),"INVALID_COUNTS");
    db.readyExternal(id,body.name(),body.version(),body.yamlPath(),json.writeValueAsString(body.classes()),
      json.writeValueAsString(body.mapping()),body.counts());
  }
  public void failExternal(String id,String lease,String error) { heartbeatExternal(id,lease); db.failExternal(id,error); }

  // ---- 파이프라인 ----
  public View create(Create request,String user) {
    var source=request.source();
    switch(request.sourceKind()) {
      case "IOT" -> {
        if(source.deviceIds()==null || source.deviceIds().isEmpty() || source.targetCount()==null) throw bad("IOT_SOURCE_REQUIRED");
        for(String device:source.deviceIds()) require(devices.registered(device),"DEVICE_NOT_REGISTERED");
      }
      case "UPLOAD" -> {
        var samples=ids(source.sampleIds());var imports=ids(source.importIds());
        if(samples.isEmpty() && imports.isEmpty()) throw bad("UPLOAD_SOURCE_REQUIRED");
        require(samples.isEmpty() || db.sampleCount(samples)==samples.size(),"SAMPLE_NOT_FOUND");
        require(imports.isEmpty() || db.importCount(imports)==imports.size(),"IMPORT_NOT_FOUND");
      }
      case "ROBOFLOW" -> { if(request.external().isEmpty()) throw bad("ROBOFLOW_SOURCE_REQUIRED"); }
      default -> { }
    }
    require(new HashSet<>(request.external().stream().map(ExternalChoice::id).toList()).size()==request.external().size(),"DUPLICATE_EXTERNAL");
    for(var choice:request.external()) {
      var external=db.external(choice.id().toString());
      require(external!=null && !external.status().equals("FAILED"),"EXTERNAL_NOT_AVAILABLE");
      require(!inBaseline(external.workspace(),external.project(),external.version()),"ALREADY_IN_BASELINE");
    }
    String base=request.options().baseModel();
    if(base.startsWith("release:")) require(runs.released(base.substring(8))==1,"BASE_MODEL_NOT_FOUND");
    String id=UUID.randomUUID().toString();
    // 이름은 학습이 끝난 뒤 Gemma가 추천한다. 그때까지 임시 이름을 쓴다.
    db.insert(id,"이름 생성 대기",request.sourceKind(),json.writeValueAsString(source),
      json.writeValueAsString(request.external()),json.writeValueAsString(request.options()),user);
    // 즉시 진행할 수 있으면 진행하고, 실패하면 주기 진행에서 다시 시도한다.
    try { advance(id); } catch(RuntimeException e) { log.info("파이프라인 첫 진행 보류: {} {}",id,e.getMessage()); }
    return view(db.pipeline(id));
  }
  static List<String> ids(List<UUID> values) { return values==null?List.of():values.stream().map(UUID::toString).distinct().toList(); }
  // ---- 전역 자동 파이프라인 ----
  static final java.time.ZoneId KST=java.time.ZoneId.of("Asia/Seoul");
  static final int AUTO_LIMIT=500;
  public AutoStatus autoStatus() {
    var settings=db.autoSettings();
    var now=java.time.ZonedDateTime.now(KST);
    var next=now.toLocalDate().atTime(settings.runAt()).atZone(KST);
    if(Objects.equals(settings.lastRunDate(),now.toLocalDate()) || next.isBefore(now)) next=next.plusDays(1);
    var list=devices.devices();
    return new AutoStatus(settings,runs.unusedIotCount(),db.labelingQueued(),
      labelMapper.discardedSince(now.toLocalDate().atStartOfDay(KST).toOffsetDateTime()),
      list.size(),(int)list.stream().filter(d -> d.online()).count(),next.toOffsetDateTime(),db.latestAuto());
  }
  public AutoStatus updateAuto(AutoUpdate update) {
    db.updateAutoSettings(update.enabled(),update.runAt(),update.minSamples(),update.epochs(),update.basePolicy());
    return autoStatus();
  }
  /** 관리자가 '지금 실행'을 누르면 시각과 무관하게 새 이미지가 1장 이상이면 실행한다. */
  public View runAutoNow() {
    String id=tx.execute(status -> startAuto(1,true));
    require(id!=null,"NO_NEW_IMAGES");
    return view(db.pipeline(id));
  }
  @Scheduled(initialDelayString="${app.pipelines.auto-initial-delay-ms:20000}",fixedDelayString="${app.pipelines.auto-interval-ms:60000}")
  public void autoTick() {
    try { tx.executeWithoutResult(status -> {
      var settings=db.autoSettings();
      var now=java.time.ZonedDateTime.now(KST);
      if(!settings.enabled() || Objects.equals(settings.lastRunDate(),now.toLocalDate()) || now.toLocalTime().isBefore(settings.runAt())) return;
      startAuto(settings.minSamples(),false);
    }); } catch(Exception e) { log.warn("자동 파이프라인 트리거 실패",e); }
  }
  /** 조건을 만족하면 AUTO 파이프라인을 만들고 id를 돌려준다. 오늘 실행 여부와 결과를 기록한다. */
  String startAuto(int minimum,boolean manual) {
    var settings=db.lockAutoSettings();
    var today=java.time.LocalDate.now(KST);
    if(runs.activeTasks()>0 || !db.active().isEmpty()) {
      // 학습이 진행 중이면 오늘 기록을 남기지 않고 다음 주기에 다시 확인한다.
      if(manual) throw new ResponseStatusException(HttpStatus.CONFLICT,"TRAINING_BUSY");
      return null;
    }
    int pending=runs.unusedIotCount();
    if(pending<minimum) {
      db.autoRan(today,"새 이미지 "+pending+"장으로 기준 "+minimum+"장 미만이라 건너뜀");
      return null;
    }
    String latest=runs.latestRelease();
    String base=settings.basePolicy().equals("LATEST") && latest!=null?"release:"+latest:"default";
    String id=UUID.randomUUID().toString();
    db.insert(id,"이름 생성 대기","AUTO","{}","[]",json.writeValueAsString(new Options(base,settings.epochs(),0.6)),manual?"auto:manual":"auto:schedule");
    db.autoRan(today,(manual?"수동 실행":"예약 실행")+" · 새 이미지 "+pending+"장");
    return id;
  }

  // ---- Gemma 이름 추천 ----
  public NamingJob claimNaming() {
    String id=db.claimNaming();
    if(id==null) return null;
    var p=db.pipeline(id);
    var context=new LinkedHashMap<String,Object>();
    context.put("sourceKind",p.sourceKind());
    context.put("stage",p.stage());
    context.put("images",p.sampleCount());
    context.put("options",json.readTree(p.options()));
    var names=new ArrayList<String>();
    for(var choice:json.readValue(p.external(),ExternalChoice[].class)) {
      var external=db.external(choice.id().toString());
      if(external!=null) names.add(external.workspace()+"/"+external.project()+(external.version()==null?"":" v"+external.version()));
    }
    context.put("roboflow",names);
    if(p.runId()!=null) {
      var run=runs.run(p.runId());
      var summary=json.readTree(run.summary());
      context.put("runState",run.state());
      context.put("selected",summary.path("selected"));
      context.put("siteSplit",summary.path("site"));
      context.put("deltaMap50_95",summary.path("evaluation").path("deltaMap50_95"));
      context.put("eligible",summary.path("evaluation").path("eligibleForPromotion"));
      context.put("error",summary.path("error"));
    } else context.put("message",p.message());
    return new NamingJob(id,json.writeValueAsString(context));
  }
  public void name(String id,String name) {
    String value=name.strip().replaceAll("\\s+"," ");
    if(value.length()>40) value=value.substring(0,40).strip();
    require(!value.isEmpty(),"INVALID_NAME");
    String unique=value;
    for(int n=2;db.sameName(id,unique)>0 && n<100;n++) unique=value+" #"+n;
    require(db.named(id,unique)==1,"NAMING_LEASE_LOST");
    var p=db.pipeline(id);
    if(p.runId()!=null) db.renameRun(p.runId(),unique);
  }

  public void cancel(String id) {
    require(db.pipeline(id)!=null,"PIPELINE_NOT_FOUND");
    require(db.cancel(id)==1,"PIPELINE_ALREADY_STARTED");
  }
  /**
   * 끝난 파이프라인과 그 학습 실행·작업 기록·실행 폴더를 지운다. 진행 중이면 먼저 취소·중단해야 하고,
   * 후보로 등록된 모델은 배포 이력·베이스 모델로 참조되므로 지우지 않는다. 수집·업로드한 이미지는 남긴다.
   */
  public void delete(String id) {
    String runId=tx.execute(status -> {
      var p=db.pipeline(id);
      if(p==null) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"PIPELINE_NOT_FOUND");
      require(!Set.of("COLLECTING","LABELING","DATASET").contains(p.stage()),"PIPELINE_ACTIVE");
      if(p.runId()!=null) {
        var run=runs.run(p.runId());
        require(run==null || !Set.of("PREPARING","DATASET_READY","TRAINING","TRAINED","EVALUATING").contains(run.state()),"PIPELINE_ACTIVE");
        require(db.activeTasks(p.runId())==0,"PIPELINE_ACTIVE");
        require(deployments.model(p.runId())==null,"MODEL_REGISTERED");
      }
      require(db.delete(id)==1,"PIPELINE_NOT_FOUND");
      if(p.runId()!=null) { db.deleteTasks(p.runId()); db.deleteRun(p.runId()); }
      return p.runId();
    });
    if(runId==null || runsRoot==null) return;
    var dir=runsRoot.resolve(UUID.fromString(runId).toString()).normalize();
    if(!dir.startsWith(runsRoot) || !java.nio.file.Files.isDirectory(dir)) return;
    try(var paths=java.nio.file.Files.walk(dir)) {
      paths.sorted(Comparator.reverseOrder()).forEach(path -> path.toFile().delete());
    } catch(Exception e) { log.warn("학습 실행 폴더 삭제 실패: {}",dir,e); }
  }
  public List<View> list() { return db.pipelines().stream().map(this::view).toList(); }
  View view(Pipeline pipeline) {
    if(pipeline.runId()==null) return new View(pipeline,null,null,List.of());
    var run=runs.run(pipeline.runId());
    var release=deployments.model(pipeline.runId());
    return new View(pipeline,run==null?null:RunView.of(run),release,release==null?List.of():deployments.deploymentsFor(release.id()));
  }

  @Scheduled(initialDelayString="${app.pipelines.initial-delay-ms:5000}",fixedDelayString="${app.pipelines.interval-ms:5000}")
  public void tick() {
    for(String id:db.active()) {
      try { advance(id); } catch(Exception e) { log.warn("파이프라인 진행 실패: {}",id,e); }
    }
  }

  /** 한 파이프라인을 가능한 만큼 다음 단계로 진행한다. 다른 호출이 잠근 경우 건너뛴다. */
  void advance(String id) {
    tx.executeWithoutResult(status -> {
      var p=db.lock(id);
      if(p==null || !Set.of("COLLECTING","LABELING","DATASET").contains(p.stage())) return;
      var source=json.readValue(p.source(),Source.class);
      var options=json.readValue(p.options(),Options.class);
      List<Member> members=switch(p.sourceKind()) {
        case "IOT" -> db.deviceMembers(source.deviceIds(),Boolean.TRUE.equals(source.includeExisting()),p.createdAt());
        case "UPLOAD" -> db.uploadMembers(ids(source.sampleIds()),ids(source.importIds()));
        default -> List.of();
      };
      int total=members.size();
      int labeled=(int)members.stream().filter(m -> m.status().equals("LABELED")).count();
      String stage=p.stage();
      if(stage.equals("COLLECTING")) {
        if(p.sourceKind().equals("IOT") && total<source.targetCount()) {
          db.progress(id,stage,null,total,labeled,"IoT 수집 대기 "+total+"/"+source.targetCount()+"장");return;
        }
        var imports=ids(source.importIds());
        if(p.sourceKind().equals("UPLOAD") && !imports.isEmpty() && db.pendingImports(imports)>0) {
          db.progress(id,stage,null,total,labeled,"영상 프레임 추출 중 · 현재 "+total+"장");return;
        }
        stage=Set.of("EXISTING","ROBOFLOW","AUTO").contains(p.sourceKind())?"DATASET":"LABELING";
      }
      if(stage.equals("LABELING")) {
        long busy=members.stream().filter(Member::busy).count();
        if(busy>0) { db.progress(id,stage,null,total,labeled,"자동 라벨링 중 "+labeled+"/"+total+"장");return; }
        stage="DATASET";
      }
      var choices=json.readValue(p.external(),ExternalChoice[].class);
      var snapshot=new ArrayList<Map<String,Object>>();
      for(var choice:choices) {
        var external=db.external(choice.id().toString());
        if(external==null || external.status().equals("FAILED")) {
          db.progress(id,"FAILED",null,total,labeled,"로보플로우 데이터셋을 불러오지 못했습니다: "+(external==null?"없음":external.error()));return;
        }
        if(!external.status().equals("READY")) { db.progress(id,stage,null,total,labeled,"로보플로우 데이터셋 내려받는 중");return; }
        snapshot.add(Map.of("id",external.id(),"name",external.name(),"yamlPath",db.externalPath(external.id()),
          "mapping",json.readTree(external.mapping())));
      }
      List<String> sampleIds=p.sourceKind().equals("AUTO")?runs.unusedIot(AUTO_LIMIT):p.sourceKind().equals("EXISTING")?runs.unusedLabeled(200)
        :p.sourceKind().equals("ROBOFLOW")?List.of():members.stream().filter(m -> m.status().equals("LABELED")).map(Member::id).toList();
      if(Set.of("EXISTING","AUTO").contains(p.sourceKind())) { total=sampleIds.size(); labeled=total; }
      if(sampleIds.isEmpty() && snapshot.isEmpty()) {
        db.progress(id,"FAILED",null,total,labeled,"학습에 사용할 라벨링 완료 이미지가 없습니다.");return;
      }
      if(runs.activeTasks()>0) { db.progress(id,stage,null,total,labeled,"다른 학습 작업이 끝나기를 기다리는 중");return; }
      var run=training.create(p.name(),sampleIds.stream().map(UUID::fromString).toList(),options.confidence(),
        new Auto(options.epochs(),null),options.baseModel(),json.writeValueAsString(snapshot));
      db.progress(id,"RUN",run.id(),total,labeled,"학습 실행을 만들었습니다.");
    });
  }
}
