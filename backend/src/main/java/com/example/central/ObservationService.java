package com.example.central;

import com.example.central.ObservationMapper.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.nio.file.Path;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

/**
 * 영상 구간 분석(Gemma) 작업 대기열, 조건 검색, 자연어 질문 대화.
 * 추론은 호스트 워커(ai-worker/observer.py)가 하고, 중앙은 임대·검증·저장·SQL 검색만 한다.
 */
@Service
public class ObservationService {
  static final Set<String> STATES=Set.of("present","absent","unknown");
  static final String ID="[A-Za-z0-9_-]{1,64}";
  final ObservationMapper db;
  final RecordingMapper recordings;
  final RecordingService recordingFiles;
  final ObjectMapper json;
  final Path thumbnails;
  static final int MAX_THUMBNAIL=300*1024, MAX_TRACE=16*1024;

  public ObservationService(ObservationMapper db,RecordingMapper recordings,RecordingService recordingFiles,ObjectMapper json,
      @org.springframework.beans.factory.annotation.Value("${app.media-root:/app/data}") String root) {
    this.db=db;this.recordings=recordings;this.recordingFiles=recordingFiles;this.json=json;
    this.thumbnails=Path.of(root).toAbsolutePath().normalize().resolve("observations");
  }
  static ResponseStatusException error(HttpStatus status,String code) { return new ResponseStatusException(status,code); }

  // ---- 구간 분석 작업 ----
  public record Job(String batchId,String leaseToken,String deviceId,String cameraId,OffsetDateTime startedAt,OffsetDateTime endedAt,
      String recordingId,String recordingPath,double offsetSeconds,double durationSeconds,Object detections,List<YoloTrack> tracks,
      int attempt) {}
  public record Lease(@NotBlank String leaseToken) {}
  public record PersonResult(@NotBlank @Size(max=32) String trackId,@NotBlank String helmet,@NotBlank String vest,
      @NotNull @Size(max=1000) String evidence) {}
  public record Completion(@NotBlank String leaseToken,@NotBlank @Pattern(regexp="COMPLETED|SKIPPED") String status,
      @Size(max=2000) String summary,@Size(max=200) String skipReason,@NotNull @Size(max=50) List<@Valid PersonResult> persons,
      @NotBlank @Size(max=64) String model,@NotBlank @Size(max=32) String promptVersion,Integer frameCount,Integer elapsedMs,
      @Size(max=MAX_THUMBNAIL*2) String thumbnail) {}
  public record Failure(@NotBlank String leaseToken,@NotBlank @Size(max=500) String error) {}

  @Transactional
  public Job claim() throws Exception {
    db.expire();
    var claimed=db.claim(UUID.randomUUID().toString());
    if(claimed==null) return null;
    var recording=recordings.covering(claimed.deviceId(),claimed.cameraId(),claimed.startedAt());
    double offset=Duration.between(recording.startedAt(),claimed.startedAt()).toNanos()/1e9;
    double length=Duration.between(claimed.startedAt(),claimed.endedAt()).toNanos()/1e9;
    // 녹화가 구간 중간에 끝나면 녹화 안쪽까지만 분석한다.
    length=Math.min(length,recording.durationSeconds()-offset);
    Path file=recordingFiles.file(recording.id());
    return new Job(claimed.batchId(),claimed.leaseToken(),claimed.deviceId(),claimed.cameraId(),claimed.startedAt(),claimed.endedAt(),
        recording.id(),file.toString(),offset,length,json.readValue(db.payload(claimed.batchId()),Object.class),db.yoloTracks(claimed.batchId()),
        claimed.attempts());
  }

  public void heartbeat(UUID id,Lease lease) {
    if(db.heartbeat(id.toString(),lease.leaseToken())!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }

  @Transactional
  public void complete(UUID id,Completion c) throws java.io.IOException {
    String batch=id.toString();
    // 대표 이미지(박스·추적 ID를 그린 JPEG, base64)는 선택 항목이다.
    byte[] image=null;
    if(c.thumbnail()!=null) {
      try { image=Base64.getDecoder().decode(c.thumbnail()); } catch(IllegalArgumentException e) { image=new byte[0]; }
      if(image.length<3 || image.length>MAX_THUMBNAIL || (image[0]&0xff)!=0xFF || (image[1]&0xff)!=0xD8 || (image[2]&0xff)!=0xFF)
        throw error(HttpStatus.BAD_REQUEST,"INVALID_THUMBNAIL");
    }
    Set<String> known=new HashSet<>();
    for(var t:db.yoloTracks(batch)) known.add(t.trackId());
    Set<String> seen=new HashSet<>();
    for(var p:c.persons()) {
      if(!known.contains(p.trackId()) || !seen.add(p.trackId())) throw error(HttpStatus.BAD_REQUEST,"UNKNOWN_TRACK");
      if(!STATES.contains(p.helmet()) || !STATES.contains(p.vest())) throw error(HttpStatus.BAD_REQUEST,"INVALID_STATE");
    }
    boolean completed="COMPLETED".equals(c.status());
    if(completed && (c.summary()==null || c.summary().isBlank())) throw error(HttpStatus.BAD_REQUEST,"INVALID_SUMMARY");
    if(!completed && !c.persons().isEmpty()) throw error(HttpStatus.BAD_REQUEST,"INVALID_SKIP");
    if(db.complete(batch,c.leaseToken(),c.status(),completed?c.summary().strip():null,completed?null:c.skipReason(),c.model(),
        c.promptVersion(),c.frameCount(),c.elapsedMs(),image!=null)!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
    if(image!=null) {
      java.nio.file.Files.createDirectories(thumbnails);
      Path temp=java.nio.file.Files.createTempFile(thumbnails,"thumb-",".part");
      java.nio.file.Files.write(temp,image);
      java.nio.file.Files.move(temp,thumbnail(batch),java.nio.file.StandardCopyOption.REPLACE_EXISTING,java.nio.file.StandardCopyOption.ATOMIC_MOVE);
    }
    // 같은 구간을 다시 분석해도 사람 판정은 최신 결과 한 벌만 남는다.
    db.clearPersons(batch);
    for(var p:c.persons()) db.person(batch,p.trackId(),p.helmet(),p.vest(),p.evidence());
  }

  Path thumbnail(String batch) { return thumbnails.resolve(UUID.fromString(batch)+".jpg"); }

  public Path thumbnailFile(UUID id) {
    Path path=thumbnail(id.toString());
    if(!Boolean.TRUE.equals(db.hasThumbnail(id.toString())) || !java.nio.file.Files.isRegularFile(path))
      throw error(HttpStatus.NOT_FOUND,"THUMBNAIL_NOT_FOUND");
    return path;
  }

  public void fail(UUID id,Failure f) {
    if(db.fail(id.toString(),f.leaseToken(),f.error())!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }

  public void release(UUID id,Failure f) {
    if(db.release(id.toString(),f.leaseToken(),f.error())!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }

  public record Retried(int requeued) {}
  public Retried retryFailed(String deviceId) {
    if(deviceId!=null && !deviceId.matches(ID)) throw error(HttpStatus.BAD_REQUEST,"INVALID_DEVICE");
    return new Retried(db.retryFailed(blank(deviceId)));
  }

  /**
   * 워커가 죽거나 멈춰도 작업이 RUNNING·QUEUED로 남지 않게 서버가 직접 임대를 정리한다.
   * 이전에는 워커가 다음 작업을 가져갈 때만 정리해 워커가 없으면 질문이 끝없이 대기했다.
   */
  @org.springframework.scheduling.annotation.Scheduled(initialDelay=10000,fixedDelayString="${app.observations.sweep-ms:10000}")
  public void sweep() {
    db.expire();
    db.expireQuestions();
    db.expireWaitingQuestions(QUESTION_WAIT_SECONDS);
  }
  static final int QUESTION_WAIT_SECONDS=60;

  // ---- 검색 ----
  /** 검색 조건. null은 조건 없음. from/to가 비면 그쪽 기간 제한이 없다(둘 다 비면 전체 기간). */
  public record Filter(@Pattern(regexp=ID) String deviceId,@Pattern(regexp=ID) String cameraId,OffsetDateTime from,OffsetDateTime to,
      @Pattern(regexp="present|absent|unknown") String helmet,@Pattern(regexp="present|absent|unknown") String vest,
      @Size(max=100) String text) {}
  public record Person(String trackId,String helmet,String vest,String evidence,String yoloHelmet,String yoloVest) {}
  public record SegmentView(String batchId,String deviceId,String cameraId,OffsetDateTime startedAt,OffsetDateTime endedAt,
      String summary,List<Person> persons,List<String> matched,String recordingId,Double offsetSeconds,String playbackPath,
      String thumbnailPath) {}

  public Filter normalize(Filter f) {
    OffsetDateTime to=f.to()==null?null:DetectionService.utc(f.to());
    OffsetDateTime from=f.from()==null?null:DetectionService.utc(f.from());
    if(from!=null && to!=null && !to.isAfter(from)) throw error(HttpStatus.BAD_REQUEST,"INVALID_RANGE");
    String text=f.text()==null || f.text().isBlank()?null:f.text().strip();
    return new Filter(blank(f.deviceId()),blank(f.cameraId()),from,to,blank(f.helmet()),blank(f.vest()),text);
  }
  static String blank(String v) { return v==null || v.isBlank()?null:v; }

  public List<SegmentView> search(Filter raw) {
    Filter f=normalize(raw);
    String pattern=f.text()==null?null:"%"+f.text().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%";
    List<SegmentView> out=new ArrayList<>();
    for(var s:db.search(f.deviceId(),f.cameraId(),f.from(),f.to(),f.helmet(),f.vest(),pattern)) {
      List<Person> persons=json.readValue(s.persons(),new TypeReference<List<Person>>(){});
      // 조건에 맞은 사람을 결과 화면에서 강조한다. 문구 조건만 있으면 강조하지 않는다.
      List<String> matched=f.helmet()==null && f.vest()==null ? List.of() : persons.stream()
          .filter(p->(f.helmet()==null || f.helmet().equals(p.helmet())) && (f.vest()==null || f.vest().equals(p.vest())))
          .map(Person::trackId).toList();
      out.add(new SegmentView(s.batchId(),s.deviceId(),s.cameraId(),s.startedAt(),s.endedAt(),s.summary(),persons,matched,
          s.recordingId(),s.offsetSeconds(),s.recordingId()==null?null:"/api/recordings/"+s.recordingId()+"/video",
          s.hasThumbnail()?"/api/observations/"+s.batchId()+"/thumbnail":null));
    }
    return out;
  }

  // ---- 질문 대화 ----
  public record Ask(UUID conversationId,@NotBlank @Size(max=500) String message) {}
  public record QuestionView(String id,String conversationId,int seq,String message,String status,Filter filter,String reply,
      String error,Integer elapsedMs,OffsetDateTime createdAt,OffsetDateTime finishedAt,Object trace,List<SegmentView> results) {}
  public record QuestionJob(String id,String leaseToken,String message,OffsetDateTime now,List<Map<String,Object>> history,List<Camera> cameras) {}
  public record Answer(@NotBlank String leaseToken,@NotNull @Valid Filter filter,@NotBlank @Size(max=500) String reply,Integer elapsedMs,
      Map<String,Object> trace) {}

  @Transactional
  public QuestionView ask(String user,Ask body) {
    String conversation;
    if(body.conversationId()==null) {
      conversation=UUID.randomUUID().toString();
      db.conversation(conversation,user);
    } else {
      conversation=body.conversationId().toString();
      // 같은 대화의 질문 순서를 지키려고 대화 행을 잠그고, 답을 기다리는 질문이 있으면 받지 않는다.
      if(db.lockConversation(conversation)==null) throw error(HttpStatus.NOT_FOUND,"CONVERSATION_NOT_FOUND");
      if(db.pending(conversation)) throw error(HttpStatus.CONFLICT,"QUESTION_PENDING");
    }
    String id=UUID.randomUUID().toString();
    db.ask(id,conversation,body.message().strip());
    return view(db.question(id),false);
  }

  public QuestionView question(UUID id) {
    var q=db.question(id.toString());
    if(q==null) throw error(HttpStatus.NOT_FOUND,"QUESTION_NOT_FOUND");
    return view(q,true);
  }

  public List<QuestionView> conversation(UUID id) {
    if(!db.conversationExists(id.toString())) throw error(HttpStatus.NOT_FOUND,"CONVERSATION_NOT_FOUND");
    return db.conversationQuestions(id.toString()).stream().map(q->view(q,false)).toList();
  }

  QuestionView view(Question q,boolean results) {
    Filter filter=q.filter()==null?null:json.readValue(q.filter(),Filter.class);
    return new QuestionView(q.id(),q.conversationId(),q.seq(),q.message(),q.status(),filter,q.reply(),q.error(),q.elapsedMs(),
        q.createdAt(),q.finishedAt(),q.trace()==null?null:json.readValue(q.trace(),Object.class),results && filter!=null && "COMPLETED".equals(q.status())?search(filter):null);
  }

  @Transactional
  public QuestionJob claimQuestion() {
    db.expireQuestions();
    var q=db.claimQuestion(UUID.randomUUID().toString());
    if(q==null) return null;
    List<Map<String,Object>> history=new ArrayList<>();
    for(var h:db.history(q.conversationId(),q.seq())) {
      Map<String,Object> item=new LinkedHashMap<>();
      item.put("message",h.message());
      item.put("filter",json.readValue(h.filter(),Object.class));
      history.add(0,item);
    }
    return new QuestionJob(q.id(),q.leaseToken(),q.message(),OffsetDateTime.now(),history,db.cameras());
  }

  public void answer(UUID id,Answer a) {
    Filter filter;
    try { filter=normalize(a.filter()); }
    catch(ResponseStatusException e) {
      questionFailed(id,new Failure(a.leaseToken(),"질문에서 올바른 기간을 만들지 못했습니다. 기간을 바꿔 다시 질문해 주세요."));
      return;
    }
    String trace=a.trace()==null?null:json.writeValueAsString(a.trace());
    if(trace!=null && trace.length()>MAX_TRACE) trace=json.writeValueAsString(Map.of("truncated",true));
    if(db.answer(id.toString(),a.leaseToken(),json.writeValueAsString(filter),a.reply().strip(),a.elapsedMs(),trace)!=1)
      throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }

  public List<ConversationSummary> conversations(String user) { return db.conversations(user,50); }

  /** 본인 대화만 지운다. 답을 기다리는 질문이 있어도 지울 수 있다(워커의 늦은 답은 행이 없어 저장되지 않는다). */
  public void deleteConversation(String user,UUID id) {
    String owner=db.conversationOwner(id.toString());
    if(owner==null || !owner.equals(user)) throw error(HttpStatus.NOT_FOUND,"CONVERSATION_NOT_FOUND");
    db.deleteConversation(id.toString());
  }

  /** 답을 기다리는 질문만 중지할 수 있다. 이미 끝난 질문은 409. */
  public QuestionView cancel(UUID id) {
    if(db.question(id.toString())==null) throw error(HttpStatus.NOT_FOUND,"QUESTION_NOT_FOUND");
    if(db.cancel(id.toString())!=1) throw error(HttpStatus.CONFLICT,"QUESTION_FINISHED");
    return view(db.question(id.toString()),false);
  }

  public void questionFailed(UUID id,Failure f) {
    if(db.questionFailed(id.toString(),f.leaseToken(),f.error())!=1) throw error(HttpStatus.CONFLICT,"LEASE_LOST");
  }
}
