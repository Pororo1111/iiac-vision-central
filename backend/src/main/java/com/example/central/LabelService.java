package com.example.central;

import com.example.central.LabelModels.*;
import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.*;
import javax.imageio.ImageIO;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

@Service
public class LabelService {
  final LabelMapper db;
  final ObjectMapper json;
  final Path root;
  final org.springframework.transaction.support.TransactionTemplate transactions;
  public LabelService(LabelMapper db,ObjectMapper json,@Value("${app.media-root:/app/data}") String root, org.springframework.transaction.PlatformTransactionManager manager) {
    this.transactions=new org.springframework.transaction.support.TransactionTemplate(manager);
    this.db=db; this.json=json; this.root=Path.of(root).toAbsolutePath().normalize();
  }
  static void require(boolean ok,String message) {
    if (!ok) throw new ResponseStatusException(HttpStatus.CONFLICT,message);
  }
  public Sample sample(String id) {
    var s=db.sample(id);
    if(s==null) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"SAMPLE_NOT_FOUND");
    return s;
  }
  public Path image(String id) { sample(id); return root.resolve(UUID.fromString(id)+".jpg"); }
  public Sample upload(MultipartFile file,String title,String device,String camera,OffsetDateTime captured) throws Exception {
    return store(file,title,device,camera,captured,"IOT",null);
  }
  /** 관리자가 직접 올린 이미지. 같은 이미지를 다시 올리면 기존 샘플을 돌려준다. */
  public Sample uploadManual(MultipartFile file,String title) throws Exception {
    return store(file,title,null,null,null,"UPLOAD",null);
  }
  /** 영상 가져오기 작업에서 워커가 추출한 프레임. */
  public Sample uploadFrame(MultipartFile file,String title,String kind,String importId) throws Exception {
    return store(file,title,null,null,null,kind,importId);
  }
  private Sample existing(String device,String camera,OffsetDateTime captured,String source,String importId,String hash) {
    if(device!=null) return db.duplicate(device,camera,captured,hash);
    if(importId!=null) return db.imported(importId,hash);
    return source.equals("UPLOAD")?db.uploaded(hash):null;
  }
  private Sample store(MultipartFile file,String title,String device,String camera,OffsetDateTime captured,String source,String importId) throws Exception {
    if(file.isEmpty() || file.getSize()>10*1024*1024) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"INVALID_IMAGE_SIZE");
    byte[] normalized;
    int width,height;
    try(var input=ImageIO.createImageInputStream(file.getInputStream())) {
      var readers=ImageIO.getImageReaders(input);
      if(!readers.hasNext()) throw new IOException();
      var reader=readers.next();
      try {
        reader.setInput(input);
        String format=reader.getFormatName();
        if(!format.equalsIgnoreCase("JPEG") && !format.equalsIgnoreCase("PNG")) throw new IOException();
        width=reader.getWidth(0); height=reader.getHeight(0);
        if(width<32 || height<32 || (long)width*height>20_000_000) throw new IOException();
        var decoded=reader.read(0);
        var rgb=new java.awt.image.BufferedImage(width,height,java.awt.image.BufferedImage.TYPE_INT_RGB);
        var graphics=rgb.createGraphics();
        graphics.setColor(java.awt.Color.WHITE); graphics.fillRect(0,0,width,height);
        graphics.drawImage(decoded,0,0,null); graphics.dispose();
        var out=new ByteArrayOutputStream(); ImageIO.write(rgb,"jpg",out); normalized=out.toByteArray();
      } finally { reader.dispose(); }
    } catch(IOException e) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"INVALID_IMAGE"); }
    String id=UUID.randomUUID().toString();
    String hash=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(normalized));
    var previous=existing(device,camera,captured,source,importId,hash);
    if(previous!=null) return previous;
    Files.createDirectories(root);
    Path destination=root.resolve(id+".jpg");
    Files.write(destination,normalized,StandardOpenOption.CREATE_NEW);
    var s=new Sample(id,hash,title,"image/jpeg",width,height,device,camera,captured,"NEW",0,null,null,OffsetDateTime.now(),source,importId);
    try { transactions.executeWithoutResult(tx -> {
      db.insert(s);
      db.enqueue(UUID.randomUUID().toString(),id,0,source.equals("IOT")?"수집 이미지 자동 라벨링":"업로드 이미지 자동 라벨링","AUTO_LABEL");
    }); } catch(RuntimeException e) {
      Files.deleteIfExists(destination);
      if(e instanceof org.springframework.dao.DuplicateKeyException) {
        var again=existing(device,camera,captured,source,importId,hash);
        if(again!=null) return again;
      }
      throw e;
    }
    return sample(id);
  }
  @Transactional
  public Sample review(String id,Review review,String username) {
    var s=db.lock(id); require(s!=null,"SAMPLE_NOT_FOUND");
    require(s.revision()==review.revision(),"REVISION_CONFLICT");
    db.expire();
    require(db.live(id)==0,"LABEL_JOB_ACTIVE");
    String annotation=json.writeValueAsString(review.result());
    db.review(id,review.status(),annotation);
    db.audit(id,s.revision()+1,username,review.status(),annotation);
    return sample(id);
  }
  @Transactional
  public Job enqueue(CreateJob request) {
    String id=request.sampleId().toString(); var s=db.lock(id);
    require(s!=null,"SAMPLE_NOT_FOUND");
    require(!s.status().equals("EXCLUDED"),"SAMPLE_EXCLUDED");
    db.expire(); require(db.live(id)==0,"LABEL_JOB_ACTIVE");
    String job=UUID.randomUUID().toString();
    db.enqueue(job,id,s.revision(),request.instruction(),"ANALYSIS");
    return db.job(job);
  }
  @Transactional
  public Job retryLabel(String id) {
    var s=db.lock(id); require(s!=null,"SAMPLE_NOT_FOUND");
    require(s.status().equals("NEW"),"SAMPLE_ALREADY_PROCESSED");
    db.expire(); require(db.live(id)==0,"LABEL_JOB_ACTIVE");
    String job=UUID.randomUUID().toString();
    db.enqueue(job,id,s.revision(),"수집 이미지 자동 라벨링","AUTO_LABEL");
    return db.job(job);
  }
  @Transactional
  public List<Job> enqueueBatch(CreateBatch request) {
    require(new HashSet<>(request.sampleIds()).size()==request.sampleIds().size(),"DUPLICATE_SAMPLE");
    return request.sampleIds().stream().sorted().map(id -> enqueue(new CreateJob(id,request.instruction()))).toList();
  }
  @Transactional
  public Job claim() { db.expire(); return db.claim(UUID.randomUUID().toString()); }
  @Transactional
  public void complete(String id,Complete result) {
    // 같은 샘플의 관리자 편집과 작업 완료를 같은 순서로 잠근다.
    Job initial=db.peek(id); require(initial!=null,"JOB_NOT_FOUND");
    var s=db.lock(initial.sampleId());
    require(db.heartbeat(id,result.leaseToken().toString())==1,"LEASE_EXPIRED");
    require(s.revision()==initial.sampleRevision(),"REVISION_CONFLICT");
    require(result.result().boxes()==null || result.result().boxes().isEmpty(),"BOX_LABELING_DISABLED");
    String proposal=json.writeValueAsString(Map.of("result",result.result(),"model",initial.model(),
        "modelDigest",result.modelDigest(),"instruction",initial.instruction(),"jobId",id));
    require(db.proposal(s.id(),s.revision(),proposal)==1,"REVISION_CONFLICT");
    db.result(id,proposal);
    db.finish(id,"COMPLETED",null);
    // 4개 클래스 중 아무것도 없는 이미지는 학습에 도움이 되지 않으므로 라벨링 단계에서 삭제한다.
    if(result.result().labels().isEmpty()) discard(s,"NO_OBJECTS");
  }
  /** 샘플·작업 기록을 지우고 집계용 기록만 남긴다. 파일은 커밋 뒤 지우며, 그 전에 멈추면 기동 시 MediaCleanup이 지운다. */
  void discard(Sample s,String reason) {
    db.discarded(s.id(),s.source(),s.deviceId(),reason);
    db.queueDeletion(s.id());
    db.deleteReviews(s.id()); db.deleteJobs(s.id()); db.deleteSample(s.id());
    String id=s.id();
    org.springframework.transaction.support.TransactionSynchronizationManager.registerSynchronization(
      new org.springframework.transaction.support.TransactionSynchronization() {
        @Override public void afterCommit() {
          try { Files.deleteIfExists(root.resolve(UUID.fromString(id)+".jpg")); db.deletionDone(id); } catch(Exception ignored) { }
        }
      });
  }
  @Transactional
  public void fail(String id,Failure failure) {
    require(db.heartbeat(id,failure.leaseToken().toString())==1,"LEASE_EXPIRED");
    db.finish(id,"FAILED",failure.error());
  }
}
