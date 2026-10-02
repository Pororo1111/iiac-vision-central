package com.example.central;

import com.example.central.DetectionMapper.*;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
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

  public DetectionService(DetectionMapper db,CentralMapper devices,ObservationMapper observations,ObjectMapper json) {
    this.db=db;this.devices=devices;this.observations=observations;this.json=json;
  }
  static ResponseStatusException error(HttpStatus status,String code) { return new ResponseStatusException(status,code); }

  /** 저장했으면 false, 같은 배치를 다시 받았으면 true(duplicate). */
  @Transactional
  public boolean receive(String authenticated,DetectionBatch b) throws Exception {
    if(!authenticated.equals(b.deviceId())) throw error(HttpStatus.FORBIDDEN,"DEVICE_MISMATCH");
    if(!devices.registered(authenticated)) throw error(HttpStatus.CONFLICT,"DEVICE_NOT_REGISTERED");
    OffsetDateTime start=utc(b.startedAt()),end=utc(b.endedAt());
    if(!end.isAfter(start) || Duration.between(start,end).compareTo(MAX_WINDOW)>0) throw error(HttpStatus.BAD_REQUEST,"INVALID_WINDOW");
    if(start.isAfter(OffsetDateTime.now().plusMinutes(5))) throw error(HttpStatus.BAD_REQUEST,"FUTURE_BATCH");
    validate(b,start,end);

    String payload=json.writeValueAsString(Map.of("frames",b.frames(),"cuts",b.cuts()));
    String sha=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(json.writeValueAsBytes(b)));
    Summary summary=summarize(b);
    if(db.insertBatch(b,RULE,b.frames().size(),b.cuts().size(),summary,payload,sha)==0) {
      // 재전송은 추가 저장 없이 성공으로 답한다. 같은 ID에 다른 내용은 덮어쓰지 않는다.
      var existing=db.existing(b.batchId().toString());
      if(existing==null || !existing.deviceId().equals(authenticated) || !existing.payloadSha256().equals(sha))
        throw error(HttpStatus.CONFLICT,"BATCH_ID_CONFLICT");
      return true;
    }
    for(Track t:summary.tracks()) db.insertTrack(b,t);
    // 구간 분석(Gemma) 작업을 같은 트랜잭션에서 등록한다. 녹화가 있어야 워커가 가져간다.
    observations.enqueue(b.batchId().toString(),b.deviceId(),b.cameraId(),start,end);
    return false;
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

  public List<TrackView> tracks(String deviceId,String cameraId,OffsetDateTime from,OffsetDateTime to) {
    if(!deviceId.matches("[A-Za-z0-9_-]{1,64}") || !cameraId.matches("[A-Za-z0-9_-]{1,64}")) throw error(HttpStatus.BAD_REQUEST,"INVALID_CAMERA");
    if(!to.isAfter(from) || Duration.between(from,to).compareTo(Duration.ofDays(1))>0) throw error(HttpStatus.BAD_REQUEST,"INVALID_RANGE");
    return db.tracks(deviceId,cameraId,utc(from),utc(to));
  }
}
