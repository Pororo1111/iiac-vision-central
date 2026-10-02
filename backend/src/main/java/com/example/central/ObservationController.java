package com.example.central;

import com.example.central.ObservationService.*;
import jakarta.validation.Valid;
import java.security.Principal;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
public class ObservationController {
  final ObservationService service;
  public ObservationController(ObservationService service) { this.service=service; }

  // ---- 워커(loopback, 워커 Bearer) ----
  @PostMapping("/internal/ai/observations/claim")
  public ResponseEntity<Job> claim() throws Exception {
    var job=service.claim();
    return job==null?ResponseEntity.noContent().build():ResponseEntity.ok(job);
  }
  @PostMapping("/internal/ai/observations/{id}/heartbeat")
  public void heartbeat(@PathVariable UUID id,@Valid @RequestBody Lease body) { service.heartbeat(id,body); }
  @PostMapping("/internal/ai/observations/{id}/complete")
  public void complete(@PathVariable UUID id,@Valid @RequestBody Completion body) throws Exception { service.complete(id,body); }
  @PostMapping("/internal/ai/observations/{id}/fail")
  public void fail(@PathVariable UUID id,@Valid @RequestBody Failure body) { service.fail(id,body); }
  @PostMapping("/internal/ai/observations/{id}/release")
  public void release(@PathVariable UUID id,@Valid @RequestBody Failure body) { service.release(id,body); }

  @PostMapping("/internal/ai/search/questions/claim")
  public ResponseEntity<QuestionJob> claimQuestion() {
    var job=service.claimQuestion();
    return job==null?ResponseEntity.noContent().build():ResponseEntity.ok(job);
  }
  @PostMapping("/internal/ai/search/questions/{id}/answer")
  public void answer(@PathVariable UUID id,@Valid @RequestBody Answer body) { service.answer(id,body); }
  @PostMapping("/internal/ai/search/questions/{id}/fail")
  public void questionFailed(@PathVariable UUID id,@Valid @RequestBody Failure body) { service.questionFailed(id,body); }

  // ---- 관리자 ----
  @GetMapping("/api/observations/search")
  public List<SegmentView> search(@RequestParam(required=false) String deviceId,@RequestParam(required=false) String cameraId,
      @RequestParam(required=false) @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime from,
      @RequestParam(required=false) @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime to,
      @RequestParam(required=false) String helmet,@RequestParam(required=false) String vest,@RequestParam(required=false) String text) {
    var filter=new Filter(deviceId,cameraId,from,to,helmet,vest,text);
    if((deviceId!=null && !deviceId.matches(ObservationService.ID)) || (cameraId!=null && !cameraId.matches(ObservationService.ID))
        || (helmet!=null && !ObservationService.STATES.contains(helmet)) || (vest!=null && !ObservationService.STATES.contains(vest))
        || (text!=null && text.length()>100))
      throw ObservationService.error(org.springframework.http.HttpStatus.BAD_REQUEST,"INVALID_FILTER");
    return service.search(filter);
  }
  @GetMapping("/api/observations/{id}/thumbnail")
  public ResponseEntity<org.springframework.core.io.FileSystemResource> thumbnail(@PathVariable UUID id) {
    return ResponseEntity.ok().contentType(org.springframework.http.MediaType.IMAGE_JPEG)
        .cacheControl(org.springframework.http.CacheControl.noStore().cachePrivate())
        .body(new org.springframework.core.io.FileSystemResource(service.thumbnailFile(id)));
  }
  @PostMapping("/api/search/questions/{id}/cancel")
  public QuestionView cancel(@PathVariable UUID id) { return service.cancel(id); }
  @PostMapping("/api/search/questions")
  public QuestionView ask(Principal p,@Valid @RequestBody Ask body) { return service.ask(p.getName(),body); }
  @GetMapping("/api/search/questions/{id}")
  public QuestionView question(@PathVariable UUID id) { return service.question(id); }
  @PostMapping("/api/observations/retry-failed")
  public ObservationService.Retried retryFailed(@RequestParam(required=false) String deviceId) { return service.retryFailed(deviceId); }
  @GetMapping("/api/search/conversations")
  public List<ObservationMapper.ConversationSummary> conversations(Principal p) { return service.conversations(p.getName()); }
  @DeleteMapping("/api/search/conversations/{id}")
  public ResponseEntity<Void> deleteConversation(Principal p,@PathVariable UUID id) {
    service.deleteConversation(p.getName(),id);
    return ResponseEntity.noContent().build();
  }
  @GetMapping("/api/search/conversations/{id}")
  public List<QuestionView> conversation(@PathVariable UUID id) { return service.conversation(id); }
}
