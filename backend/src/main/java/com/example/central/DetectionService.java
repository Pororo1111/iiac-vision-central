package com.example.central;

import com.example.central.DetectionMapper.*;
import java.io.IOException;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.*;
import javax.imageio.ImageIO;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

/**
 * 탐지 배치를 검증·저장하고 사람 추적별 보호구 연결을 요약한다(영상 분석·검색 P2).
 *
 * <p>연결 규칙(RULE): 안전모 박스 중심이 사람 박스 가로 범위 안, 세로로 머리 영역(위 10% 밖 ~ 위 35%)에 있으면 후보다.
 * 조끼 박스 중심은 사람 박스 가로 범위 안, 세로 15~85% 영역이다. 보호구 박스 하나는 머리(또는 몸통) 중심에 가장
 * 가까운 사람 한 명에게만 연결한다. 추적의 관찰 프레임 중 2프레임 이상·20% 이상 연결되면 present, 아니면 unknown이다.
 * 탐지되지 않았다는 이유로 absent를 만들지 않는다. 손에 든 안전모처럼 머리 영역 밖의 박스는 연결하지 않는다.
 */
@Service
public class DetectionService {
  static final String RULE="ppe-assoc-v1";
  static final Duration MAX_WINDOW=Duration.ofSeconds(60);
  final DetectionMapper db;
  final CentralMapper devices;
  final ObservationMapper observations;
  final ObjectMapper json;
  final FpCheckMapper fpChecks;
  final List<String> reviewExclude;
  final Path snapshots;

  public DetectionService(DetectionMapper db,CentralMapper devices,ObservationMapper observations,ObjectMapper json,
      FpCheckMapper fpChecks,@Value("${app.media-root:/app/data}") String root,
      @Value("${app.review.exclude-cameras:test-video*}") String exclude) {
    this.db=db;this.devices=devices;this.observations=observations;this.json=json;this.fpChecks=fpChecks;
    this.reviewExclude=Arrays.stream(exclude.split(",")).map(String::strip).filter(s->!s.isEmpty())
        .map(s->java.util.regex.Pattern.quote(s).replace("*","\\E.*\\Q")).toList();
    this.snapshots=Path.of(root).toAbsolutePath().normalize().resolve("detection-snapshots");
  }
  static ResponseStatusException error(HttpStatus status,String code) { return new ResponseStatusException(status,code); }

  /** 저장했으면 false, 같은 배치를 다시 받았으면 true(duplicate). source는 IOT(장치) 또는 STREAM(중앙 색인 워커). */
  @Transactional
  public boolean receive(String authenticated,DetectionBatch b,String source) throws Exception {
    if(!authenticated.equals(b.deviceId())) throw error(HttpStatus.FORBIDDEN,"DEVICE_MISMATCH");
    if(!devices.registered(authenticated)) throw error(HttpStatus.CONFLICT,"DEVICE_NOT_REGISTERED");
    OffsetDateTime start=utc(b.startedAt()),end=utc(b.endedAt());
    if(!end.isAfter(start) || Duration.between(start,end).compareTo(MAX_WINDOW)>0) throw error(HttpStatus.BAD_REQUEST,"INVALID_WINDOW");
    if(start.isAfter(OffsetDateTime.now().plusMinutes(5))) throw error(HttpStatus.BAD_REQUEST,"FUTURE_BATCH");
    validate(b,start,end);

    String payload=json.writeValueAsString(Map.of("frames",b.frames(),"cuts",b.cuts()));
    String sha=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(json.writeValueAsBytes(b)));
    Summary summary=summarize(b);
    if(db.insertBatch(b,RULE,b.frames().size(),b.cuts().size(),summary,payload,sha,source)==0) {
      // 재전송은 추가 저장 없이 성공으로 답한다. 같은 ID에 다른 내용은 덮어쓰지 않는다.
      var existing=db.existing(b.batchId().toString());
      if(existing==null || !existing.deviceId().equals(authenticated) || !existing.payloadSha256().equals(sha))
        throw error(HttpStatus.CONFLICT,"BATCH_ID_CONFLICT");
      return true;
    }
    for(Track t:summary.tracks()) db.insertTrack(b,t);
    // 구간 분석(Gemma) 작업을 같은 트랜잭션에서 등록한다. 사람이 한 번도 탐지되지 않은 구간은 분석하지 않는다.
    // 장치 배치는 사람이 나온 프레임 수(최대 4)만큼 원본 프레임을 기다린다. 중앙 색인 배치는 원본 프레임이 없다.
    long personFrames=b.frames().stream().filter(f->f.objects().stream().anyMatch(o->"person".equals(o.cls()))).count();
    if(personFrames==0) observations.enqueueSkipped(b.batchId().toString(),b.deviceId(),b.cameraId(),start,end,"no_person");
    else observations.enqueue(b.batchId().toString(),b.deviceId(),b.cameraId(),start,end,
        "IOT".equals(source)?(int)Math.min(MAX_SNAPSHOTS,personFrames):0,SNAPSHOT_WAIT_SECONDS);
    return false;
  }

  // ---- 원본 프레임 ----
  static final int MAX_SNAPSHOTS=4, MAX_SNAPSHOT_BYTES=1024*1024, SNAPSHOT_WAIT_SECONDS=120;

  /**
   * 장치가 보낸 오버레이 없는 원본 프레임을 저장한다. 저장했으면 false, 같은 이미지를 다시 받았으면 true(duplicate).
   * 배치가 아직 없으면 409 BATCH_NOT_FOUND(장치는 재시도한다). 다른 장치의 배치도 같은 응답으로 존재를 숨긴다.
   */
  @Transactional
  public boolean snapshot(String authenticated,UUID batchId,String deviceId,OffsetDateTime at,MultipartFile file) throws Exception {
    if(!authenticated.equals(deviceId)) throw error(HttpStatus.FORBIDDEN,"DEVICE_MISMATCH");
    if(file.isEmpty()) throw error(HttpStatus.BAD_REQUEST,"INVALID_IMAGE");
    if(file.getSize()>MAX_SNAPSHOT_BYTES) throw error(HttpStatus.PAYLOAD_TOO_LARGE,"FILE_TOO_LARGE");
    String id=batchId.toString();
    var batch=db.lockBatch(id);
    if(batch==null || !batch.deviceId().equals(deviceId)) throw error(HttpStatus.CONFLICT,"BATCH_NOT_FOUND");
    OffsetDateTime captured=utc(at);
    boolean known=false,ppe=false;
    for(var frame:json.readTree(batch.payload()).get("frames"))
      if(utc(OffsetDateTime.parse(frame.get("at").asString())).isEqual(captured)) {
        known=true;
        for(var o:frame.get("objects")) ppe|=FpCheckService.PPE.contains(o.get("cls").asString());
        break;
      }
    if(!known) throw error(HttpStatus.BAD_REQUEST,"SNAPSHOT_NOT_IN_BATCH");
    byte[] data=file.getBytes();
    int[] size=jpegSize(data);
    String sha=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
    String existing=db.snapshotSha(id,captured);
    if(existing!=null) {
      if(!existing.equals(sha)) throw error(HttpStatus.CONFLICT,"SNAPSHOT_CONFLICT");
      return true;
    }
    if(db.snapshotCount(id)>=MAX_SNAPSHOTS) throw error(HttpStatus.BAD_REQUEST,"TOO_MANY_SNAPSHOTS");
    Path target=snapshotFile(id,captured);
    Files.createDirectories(target.getParent());
    Path temp=Files.createTempFile(target.getParent(),"snap-",".part");
    Files.write(temp,data);
    Files.move(temp,target,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
    db.insertSnapshot(id,captured,sha,data.length,size[0],size[1]);
    // 그 프레임에 YOLO 보호구 검출이 있으면 Gemma 오탐 확인 대상으로 등록한다(V33).
    // 테스트 영상처럼 기준 자료로 만든 카메라는 제외한다(app.review.exclude-cameras, 쉼표로 구분한 * 패턴).
    if(ppe && reviewExclude.stream().noneMatch(p->batch.cameraId().matches(p))) fpChecks.enqueue(id,captured,deviceId,batch.cameraId());
    return false;
  }

  /** JPEG만 받는다. 원본 바이트를 그대로 저장해 재전송 중복 판정(SHA-256)이 장치와 같게 한다. */
  static int[] jpegSize(byte[] data) {
    try(var input=ImageIO.createImageInputStream(new java.io.ByteArrayInputStream(data))) {
      var readers=ImageIO.getImageReaders(input);
      if(!readers.hasNext()) throw new IOException();
      var reader=readers.next();
      try {
        reader.setInput(input);
        if(!reader.getFormatName().equalsIgnoreCase("JPEG")) throw new IOException();
        int width=reader.getWidth(0),height=reader.getHeight(0);
        if(width<32 || height<32 || (long)width*height>20_000_000) throw new IOException();
        reader.read(0);
        return new int[]{width,height};
      } finally { reader.dispose(); }
    } catch(IOException|RuntimeException e) { throw error(HttpStatus.BAD_REQUEST,"INVALID_IMAGE"); }
  }

  Path snapshotFile(String batch,OffsetDateTime at) {
    long micros=ChronoUnit.MICROS.between(java.time.Instant.EPOCH,at.toInstant());
    return snapshots.resolve(UUID.fromString(batch).toString()).resolve(micros+".jpg");
  }

  /** 장치 삭제 등으로 배치가 지워진 원본 프레임 폴더를 정리한다(행은 ON DELETE CASCADE로 지워진다). */
  @org.springframework.scheduling.annotation.Scheduled(initialDelay=60000,fixedDelay=3600000)
  public void sweepSnapshots() throws IOException {
    if(!Files.isDirectory(snapshots)) return;
    try(var dirs=Files.list(snapshots)) {
      for(Path dir:dirs.toList()) {
        String name=dir.getFileName().toString();
        try { UUID.fromString(name); } catch(IllegalArgumentException e) { continue; }
        if(db.batchExists(name)) continue;
        try(var files=Files.list(dir)) { for(Path f:files.toList()) Files.deleteIfExists(f); }
        Files.deleteIfExists(dir);
      }
    }
  }

  static OffsetDateTime utc(OffsetDateTime t) { return t.withOffsetSameInstant(ZoneOffset.UTC).truncatedTo(ChronoUnit.MICROS); }

  static void validate(DetectionBatch b,OffsetDateTime start,OffsetDateTime end) {
    OffsetDateTime previous=null;
    for(var frame:b.frames()) {
      OffsetDateTime at=utc(frame.at());
      if(at.isBefore(start) || !at.isBefore(end)) throw error(HttpStatus.BAD_REQUEST,"FRAME_OUTSIDE_WINDOW");
      if(previous!=null && !at.isAfter(previous)) throw error(HttpStatus.BAD_REQUEST,"FRAMES_NOT_SORTED");
      previous=at;
      Set<String> ids=new HashSet<>();
      for(var o:frame.objects()) {
        var box=o.box();
        if(box.get(0)>=box.get(2) || box.get(1)>=box.get(3)) throw error(HttpStatus.BAD_REQUEST,"INVALID_BOX");
        if(o.trackId()!=null && (!"person".equals(o.cls()) || !ids.add(o.trackId()))) throw error(HttpStatus.BAD_REQUEST,"INVALID_TRACK");
      }
    }
    for(var cut:b.cuts()) {
      var at=utc(cut);
      if(at.isBefore(start) || !at.isBefore(end)) throw error(HttpStatus.BAD_REQUEST,"CUT_OUTSIDE_WINDOW");
    }
    // 컷 전환 뒤에는 새 추적 ID여야 한다. 같은 ID가 컷 앞뒤에 모두 나오면 거부한다.
    Map<String,OffsetDateTime[]> spans=new HashMap<>();
    for(var frame:b.frames()) for(var o:frame.objects()) if(o.trackId()!=null) {
      var at=utc(frame.at());
      spans.merge(o.trackId(),new OffsetDateTime[]{at,at},(x,y)->new OffsetDateTime[]{x[0],y[1]});
    }
    for(var span:spans.values()) for(var cut:b.cuts()) {
      var at=utc(cut);
      if(at.isAfter(span[0]) && !at.isAfter(span[1])) throw error(HttpStatus.BAD_REQUEST,"TRACK_SPANS_CUT");
    }
  }

  static Summary summarize(DetectionBatch b) {
    Map<String,int[]> counts=new LinkedHashMap<>();   // frames, helmetFrames, vestFrames
    Map<String,OffsetDateTime[]> seen=new HashMap<>();
    Map<String,Double> scores=new HashMap<>();
    int untracked=0,looseHats=0,looseVests=0;
    for(var frame:b.frames()) {
      var persons=frame.objects().stream().filter(o->"person".equals(o.cls())).toList();
      Set<Integer> withHelmet=new HashSet<>(),withVest=new HashSet<>();
      for(var o:frame.objects()) {
        boolean hat="hard_hat".equals(o.cls()),vest="safety_vest".equals(o.cls());
        if(!hat && !vest) continue;
        int owner=owner(persons,o.box(),hat);
        if(owner<0) { if(hat) looseHats++; else looseVests++; }
        else (hat?withHelmet:withVest).add(owner);
      }
      OffsetDateTime at=utc(frame.at());
      for(int i=0;i<persons.size();i++) {
        var p=persons.get(i);
        if(p.trackId()==null) { untracked++; continue; }
        int[] c=counts.computeIfAbsent(p.trackId(),k->new int[3]);
        c[0]++;
        if(withHelmet.contains(i)) c[1]++;
        if(withVest.contains(i)) c[2]++;
        seen.merge(p.trackId(),new OffsetDateTime[]{at,at},(x,y)->new OffsetDateTime[]{x[0],y[1]});
        scores.merge(p.trackId(),p.score(),Math::max);
      }
    }
    List<Track> tracks=new ArrayList<>();
    counts.forEach((id,c)->tracks.add(new Track(id,seen.get(id)[0],seen.get(id)[1],c[0],scores.get(id),c[1],c[2],
        state(c[1],c[0]),state(c[2],c[0]))));
    return new Summary(tracks,untracked,looseHats,looseVests);
  }

  static String state(int linked,int frames) { return linked>=2 && linked*5>=frames ? "present" : "unknown"; }

  /** 보호구 박스를 연결할 사람의 목록 위치. 후보가 없으면 -1. */
  static int owner(List<DetectedObject> persons,List<Double> gear,boolean hat) {
    double gx=(gear.get(0)+gear.get(2))/2,gy=(gear.get(1)+gear.get(3))/2,gh=gear.get(3)-gear.get(1);
    int best=-1; double bestDistance=Double.MAX_VALUE;
    for(int i=0;i<persons.size();i++) {
      var p=persons.get(i).box();
      double x1=p.get(0),y1=p.get(1),x2=p.get(2),h=p.get(3)-y1;
      if(gx<x1 || gx>x2) continue;
      double top=hat?y1-0.10*h:y1+0.15*h,bottom=hat?y1+0.35*h:y1+0.85*h;
      if(gy<top || gy>bottom) continue;
      if(hat && gh>0.5*h) continue;
      double anchor=hat?y1+0.12*h:y1+0.45*h;
      double distance=Math.hypot(gx-(x1+x2)/2,gy-anchor)/h;
      if(distance<bestDistance) { bestDistance=distance;best=i; }
    }
    return best;
  }

  public boolean iotActive(String deviceId,String cameraId) {
    if(!deviceId.matches("[A-Za-z0-9_-]{1,64}") || !cameraId.matches("[A-Za-z0-9_-]{1,64}")) throw error(HttpStatus.BAD_REQUEST,"INVALID_CAMERA");
    return db.iotActive(deviceId,cameraId);
  }

  public List<TrackView> tracks(String deviceId,String cameraId,OffsetDateTime from,OffsetDateTime to) {
    if(!deviceId.matches("[A-Za-z0-9_-]{1,64}") || !cameraId.matches("[A-Za-z0-9_-]{1,64}")) throw error(HttpStatus.BAD_REQUEST,"INVALID_CAMERA");
    if(!to.isAfter(from) || Duration.between(from,to).compareTo(Duration.ofDays(1))>0) throw error(HttpStatus.BAD_REQUEST,"INVALID_RANGE");
    return db.tracks(deviceId,cameraId,utc(from),utc(to));
  }
}
