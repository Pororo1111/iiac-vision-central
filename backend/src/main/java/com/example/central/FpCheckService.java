package com.example.central;

import com.example.central.FpCheckMapper.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * 오탐 확인(V33)과 관리자 검토(V34). 원본 프레임의 YOLO 보호구 박스를 Gemma가 다시 보고(워커), 오탐·애매 판정이 있으면
 * 관리자 확인을 기다린다. 관리자가 박스별로 정탐·오탐을 정해 승인하면 원본과 고친 라벨(오탐 박스만 뺀 YOLO 결과)을
 * 누적 데이터셋 local:admin-fp에 넣는다. Gemma 판정만으로는 데이터셋에 넣지 않는다.
 */
@Service
public class FpCheckService {
  static final Set<String> PPE=Set.of("hard_hat","safety_vest");
  static final List<String> CLASSES=List.of("person","safety_vest","traffic_cone","hard_hat");
  static final String DATASET_URL="local:admin-fp",DATASET_NAME="admin-fp";
  final FpCheckMapper db;
  final DetectionService detections;
  final ObjectMapper json;
  final Path dataset;

  public FpCheckService(FpCheckMapper db,DetectionService detections,ObjectMapper json,
      @Value("${app.review.dataset-root:${app.media-root:/app/data}/admin-fp}") String dataset) {
    this.db=db;this.detections=detections;this.json=json;
    this.dataset=Path.of(dataset).toAbsolutePath().normalize();
  }
  static ResponseStatusException error(HttpStatus status,String code) { return new ResponseStatusException(status,code); }
  static OffsetDateTime utc(OffsetDateTime value) { return value.withOffsetSameInstant(ZoneOffset.UTC); }

  /** 배치 payload에서 그 시각 프레임의 객체 목록. 없으면 빈 목록. */
  List<JsonNode> objects(String payload,OffsetDateTime at) {
    for(var frame:json.readTree(payload).get("frames"))
      if(utc(OffsetDateTime.parse(frame.get("at").asString())).isEqual(utc(at))) {
        var list=new ArrayList<JsonNode>();
        frame.get("objects").forEach(list::add);
        return list;
      }
    return List.of();
  }
  public record Obj(int index,String cls,double score,List<Double> box) {}
  List<Obj> objs(String batchId,OffsetDateTime at) {
    var out=new ArrayList<Obj>();
    int i=0;
    for(var o:objects(db.payload(batchId),at)) {
      var box=new ArrayList<Double>();
      o.get("box").forEach(v->box.add(v.asDouble()));
      out.add(new Obj(i++,o.get("cls").asString(),o.get("score").asDouble(),box));
    }
    return out;
  }

  // ---- 워커 ----
  public record Job(String id,String leaseToken,int attempt,String batchId,OffsetDateTime at,String deviceId,String cameraId,
      String path,int width,int height,List<Obj> objects) {}
  public record Verdict(@NotNull @Min(0) @Max(99) Integer index,@NotBlank @Pattern(regexp="real|false|unsure") String verdict,
      @NotNull @Size(max=100) String seen,@NotNull @Size(max=300) String reason) {}
  public record Completion(@NotBlank String leaseToken,@NotNull @Size(max=100) List<@Valid Verdict> verdicts,
      @NotBlank @Size(max=64) String model,@NotBlank @Size(max=32) String promptVersion,Integer elapsedMs) {}
  public record Failure(@NotBlank String leaseToken,@NotBlank @Size(max=500) String error) {}

  public Job claim(String deviceId) {
    if(deviceId!=null && !deviceId.matches("[A-Za-z0-9_-]{1,64}")) throw error(HttpStatus.BAD_REQUEST,"INVALID_REQUEST");
    db.expire();
    var c=db.claim(UUID.randomUUID().toString(),deviceId);
    if(c==null) return null;
    var size=db.snapshot(c.batchId(),c.capturedAt());
    return new Job(c.id(),c.leaseToken(),c.attempts(),c.batchId(),c.capturedAt(),c.deviceId(),c.cameraId(),
        detections.snapshotFile(c.batchId(),c.capturedAt()).toString(),size==null?0:size.width(),size==null?0:size.height(),
        objs(c.batchId(),c.capturedAt()));
  }

  /** 판정은 그 프레임의 보호구 박스마다 정확히 하나씩. 오탐·애매가 하나라도 있으면 관리자 확인(PENDING)으로 둔다. */
  @Transactional
  public void complete(UUID id,Completion body) {
    var run=db.running(id.toString(),body.leaseToken());
    if(run==null) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
    var objects=objs(run.batchId(),run.capturedAt());
    var expected=new TreeSet<Integer>();
    for(var o:objects) if(PPE.contains(o.cls())) expected.add(o.index());
    var given=new TreeSet<Integer>();
    for(var v:body.verdicts()) if(!given.add(v.index())) throw error(HttpStatus.BAD_REQUEST,"INVALID_VERDICTS");
    if(!given.equals(expected)) throw error(HttpStatus.BAD_REQUEST,"INVALID_VERDICTS");
    var stored=new ArrayList<Map<String,Object>>();
    int falses=0,unsure=0;
    for(var v:body.verdicts().stream().sorted(Comparator.comparingInt(Verdict::index)).toList()) {
      var o=objects.get(v.index());
      var row=new LinkedHashMap<String,Object>();
      row.put("index",v.index());row.put("cls",o.cls());row.put("score",o.score());row.put("box",o.box());
      row.put("verdict",v.verdict());row.put("seen",v.seen());row.put("reason",v.reason());
      stored.add(row);
      if(v.verdict().equals("false")) falses++;
      if(v.verdict().equals("unsure")) unsure++;
    }
    String review=falses+unsure>0?"PENDING":"NOT_NEEDED";
    if(db.complete(id.toString(),body.leaseToken(),body.model(),body.promptVersion(),json.writeValueAsString(stored),falses,unsure,
        review,body.elapsedMs())!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }

  public void fail(UUID id,Failure body) {
    if(db.fail(id.toString(),body.leaseToken(),body.error())!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }
  public void release(UUID id,Failure body) {
    if(db.release(id.toString(),body.leaseToken(),body.error())!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }

  // ---- 관리자 조회 ----
  static final Set<String> REVIEWS=Set.of("NOT_NEEDED","PENDING","APPROVED","REJECTED");
  public record DatasetInfo(String url,int train,int val,int boxes) {}
  public record ItemView(Item item,String imagePath) {}
  public record View(Summary summary,DatasetInfo dataset,List<ItemView> items) {}
  ItemView view(Item i) { return new ItemView(i,"/api/training/fp-checks/"+i.id()+"/image"); }
  DatasetInfo datasetInfo() {
    var c=db.datasetCounts();
    return new DatasetInfo(DATASET_URL,c.train(),c.val(),c.boxes());
  }

  public List<ItemView> list(String review,String deviceId,String cameraId,OffsetDateTime from,OffsetDateTime to,int limit) {
    if(limit<1 || limit>200 || (review!=null && !REVIEWS.contains(review))) throw error(HttpStatus.BAD_REQUEST,"INVALID_REQUEST");
    return db.list(review,deviceId,cameraId,from==null?null:utc(from),to==null?null:utc(to),limit).stream().map(this::view).toList();
  }
  public View recent(String review,int limit) {
    return new View(db.summary(),datasetInfo(),list(review,null,null,null,null,limit));
  }
  public record Detail(Item item,String imagePath,int width,int height,List<Obj> objects) {}
  public Detail detail(UUID id) {
    var item=db.item(id.toString());
    if(item==null) throw error(HttpStatus.NOT_FOUND,"FP_CHECK_NOT_FOUND");
    var size=db.snapshot(item.batchId(),item.capturedAt());
    return new Detail(item,"/api/training/fp-checks/"+id+"/image",size==null?0:size.width(),size==null?0:size.height(),
        objs(item.batchId(),item.capturedAt()));
  }
  public Path image(UUID id) {
    var item=db.item(id.toString());
    if(item==null) throw error(HttpStatus.NOT_FOUND,"FP_CHECK_NOT_FOUND");
    return detections.snapshotFile(item.batchId(),item.capturedAt());
  }

  // ---- 관리자 검토 ----
  /** removed: 관리자가 오탐으로 정한 보호구 박스 index. 나머지 보호구 박스는 정탐으로 라벨에 남는다. */
  public record ReviewRequest(@NotBlank @Pattern(regexp="APPROVE|REJECT") String action,@Size(max=100) List<@NotNull Integer> removed) {}
  public record ReviewResult(Item item,DatasetInfo dataset) {}

  /**
   * 승인: 원본을 복사하고 오탐 박스만 뺀 YOLO 결과를 라벨로 쓴 뒤 외부 데이터셋 수를 갱신한다. 첫 승인은 val, 이후는 이름 해시로
   * 10%를 val에 둔다(학습에는 train·val이 모두 필요하다). DB 갱신에 실패하면 쓴 파일을 지운다.
   */
  @Transactional
  public ReviewResult review(String user,UUID id,ReviewRequest body) throws Exception {
    var row=db.lockReview(id.toString());
    if(row==null) throw error(HttpStatus.NOT_FOUND,"FP_CHECK_NOT_FOUND");
    if(!"PENDING".equals(row.reviewStatus())) throw error(HttpStatus.CONFLICT,"REVIEW_NOT_PENDING");
    var objects=objs(row.batchId(),row.capturedAt());
    var removed=new TreeSet<Integer>(body.removed()==null?List.of():body.removed());
    for(int i:removed) if(i<0 || i>=objects.size() || !PPE.contains(objects.get(i).cls())) throw error(HttpStatus.BAD_REQUEST,"INVALID_REMOVED");
    if(body.action().equals("REJECT")) {
      if(db.review(id.toString(),"REJECTED",null,user,null,null,null)!=1) throw error(HttpStatus.CONFLICT,"REVIEW_NOT_PENDING");
      return new ReviewResult(db.item(id.toString()),datasetInfo());
    }
    long micros=ChronoUnit.MICROS.between(java.time.Instant.EPOCH,row.capturedAt().toInstant());
    String name="admin_fp_"+row.batchId()+"_"+micros+".jpg";
    String split=db.approvedVal()==0?"val":(MessageDigest.getInstance("SHA-256").digest(name.getBytes())[0]&0xff)%10==0?"val":"train";
    var lines=new ArrayList<String>();
    for(var o:objects) {
      if(removed.contains(o.index()) || !CLASSES.contains(o.cls())) continue;
      double x1=clamp(o.box().get(0)),y1=clamp(o.box().get(1)),x2=clamp(o.box().get(2)),y2=clamp(o.box().get(3));
      if(x2<=x1 || y2<=y1) continue;
      lines.add(String.format(Locale.ROOT,"%d %.6f %.6f %.6f %.6f",CLASSES.indexOf(o.cls()),(x1+x2)/2,(y1+y2)/2,x2-x1,y2-y1));
    }
    Path image=dataset.resolve("images").resolve(split).resolve(name);
    Path label=dataset.resolve("labels").resolve(split).resolve(name.replace(".jpg",".txt"));
    writeDataset(detections.snapshotFile(row.batchId(),row.capturedAt()),image,label,lines);
    try {
      if(db.review(id.toString(),"APPROVED",json.writeValueAsString(removed),user,name,split,lines.size())!=1)
        throw error(HttpStatus.CONFLICT,"REVIEW_NOT_PENDING");
      syncDataset();
    } catch(RuntimeException e) {
      Files.deleteIfExists(image);Files.deleteIfExists(label);
      throw e;
    }
    return new ReviewResult(db.item(id.toString()),datasetInfo());
  }
  static double clamp(double v) { return Math.max(0,Math.min(1,v)); }

  void writeDataset(Path source,Path image,Path label,List<String> lines) throws Exception {
    for(String split:List.of("train","val"))
      for(String sub:List.of("images","labels")) Files.createDirectories(dataset.resolve(sub).resolve(split));
    Path yaml=dataset.resolve("data.yaml");
    if(!Files.exists(yaml))
      Files.writeString(yaml,"path: "+dataset+"\ntrain: images/train\nval: images/val\nnc: 4\nnames: ['person', 'safety_vest', 'traffic_cone', 'hard_hat']\n");
    Path temp=Files.createTempFile(image.getParent(),"approve-",".part");
    Files.copy(source,temp,StandardCopyOption.REPLACE_EXISTING);
    Files.move(temp,image,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
    Files.writeString(label,lines.isEmpty()?"":String.join("\n",lines)+"\n");
  }

  /** train·val이 모두 생겨야 학습에 쓸 수 있으므로 그때 외부 데이터셋으로 등록하고, 이후에는 수만 갱신한다. */
  void syncDataset() {
    var c=db.datasetCounts();
    String counts=json.writeValueAsString(Map.of("train",c.train(),"val",c.val(),"test",0,"boxes",c.boxes()));
    String yaml=dataset.resolve("data.yaml").toString();
    String existing=db.externalId(DATASET_URL);
    if(existing!=null) db.updateExternal(existing,yaml,counts);
    else if(c.train()>0 && c.val()>0) db.insertExternal(DATASET_URL,DATASET_NAME,yaml,counts);
  }

  /** 장치 삭제 등으로 승인 기록이 사라진 데이터셋 파일을 지우고 수를 다시 맞춘다(1시간마다). */
  @Scheduled(fixedDelay=3_600_000,initialDelay=120_000)
  @Transactional
  public void sweepDataset() throws Exception {
    if(!Files.isDirectory(dataset)) return;
    var known=new HashSet<>(db.datasetNames());
    for(String split:List.of("train","val")) {
      Path images=dataset.resolve("images").resolve(split);
      if(!Files.isDirectory(images)) continue;
      try(var files=Files.list(images)) {
        for(Path f:files.toList()) {
          String name=f.getFileName().toString();
          if(!name.startsWith("admin_fp_") || known.contains(name)) continue;
          Files.deleteIfExists(f);
          Files.deleteIfExists(dataset.resolve("labels").resolve(split).resolve(name.replace(".jpg",".txt")));
        }
      }
    }
    syncDataset();
  }
}
