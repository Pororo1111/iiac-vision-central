package com.example.central;

import com.example.central.FpMemoryMapper.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.nio.file.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

/**
 * 오탐 판단 기억(V37, RAG). 사람이 정탐·오탐을 판단한 보호구 박스를 CLIP 임베딩(512차원)으로 저장하고, 워커가 새 박스와
 * 같은 클래스의 비슷한 사례를 pgvector 코사인 거리로 찾는다. 임베딩 계산·확대 사진 생성은 호스트 워커가 하고, 중앙은 저장·검색만 한다.
 * 확대 사진은 media-root/fp-memory/{id}.jpg에 둔다(관리자 화면 근거 표시·Gemma 예시 이미지).
 */
@Service
public class FpMemoryService {
  static final int DIM=512;
  final FpMemoryMapper db;
  final DetectionService detections;
  final ObjectMapper json;
  final Path crops;

  public FpMemoryService(FpMemoryMapper db,DetectionService detections,ObjectMapper json,
      @Value("${app.media-root:/app/data}") String mediaRoot) {
    this.db=db;this.detections=detections;this.json=json;
    this.crops=Path.of(mediaRoot).toAbsolutePath().normalize().resolve("fp-memory");
  }
  static ResponseStatusException error(HttpStatus status,String code) { return new ResponseStatusException(status,code); }

  static String vector(List<Double> values) {
    if(values==null || values.size()!=DIM) throw error(HttpStatus.BAD_REQUEST,"INVALID_VECTOR");
    var out=new StringBuilder("[");
    for(int i=0;i<values.size();i++) {
      Double v=values.get(i);
      if(v==null || !Double.isFinite(v) || Math.abs(v)>10) throw error(HttpStatus.BAD_REQUEST,"INVALID_VECTOR");
      if(i>0) out.append(',');
      out.append(String.format(Locale.ROOT,"%.7f",v));
    }
    return out.append(']').toString();
  }
  static String box(List<Double> box) {
    if(box==null || box.size()!=4) throw error(HttpStatus.BAD_REQUEST,"INVALID_BOX");
    for(Double v:box) if(v==null || !Double.isFinite(v) || v<-0.01 || v>1.01) throw error(HttpStatus.BAD_REQUEST,"INVALID_BOX");
    if(box.get(2)<=box.get(0) || box.get(3)<=box.get(1)) throw error(HttpStatus.BAD_REQUEST,"INVALID_BOX");
    return String.format(Locale.ROOT,"[%.6f,%.6f,%.6f,%.6f]",box.get(0),box.get(1),box.get(2),box.get(3));
  }
  Path cropFile(String id) { return crops.resolve(UUID.fromString(id)+".jpg"); }

  /** 워커가 만든 확대 사진(JPEG base64, 최대 2MB)을 임시 파일을 거쳐 저장한다. */
  void writeCrop(String id,String base64) {
    byte[] data;
    try { data=Base64.getDecoder().decode(base64); } catch(IllegalArgumentException e) { throw error(HttpStatus.BAD_REQUEST,"INVALID_CROP"); }
    if(data.length<4 || data.length>2_000_000 || (data[0]&0xff)!=0xFF || (data[1]&0xff)!=0xD8) throw error(HttpStatus.BAD_REQUEST,"INVALID_CROP");
    try {
      Files.createDirectories(crops);
      Path temp=Files.createTempFile(crops,"crop-",".part");
      Files.write(temp,data);
      Files.move(temp,cropFile(id),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
    } catch(java.io.IOException e) {
      throw new IllegalStateException("확대 사진 저장 실패",e);
    }
  }

  // ---- 관리자 승인(FpCheckService.review 트랜잭션 안에서 호출) ----
  /** 승인한 사진의 보호구 박스마다 판단을 기억한다. 오탐으로 뺀 박스는 false, 남긴 박스는 real. 임베딩은 워커가 채운다. */
  public void remember(String fpCheckId,List<FpCheckService.Obj> objects,Set<Integer> removed,String user) {
    for(var o:objects) {
      if(!FpCheckService.PPE.contains(o.cls())) continue;
      db.insertAdmin(fpCheckId,o.index(),o.cls(),removed.contains(o.index())?"false":"real",box(o.box()),user);
    }
  }

  // ---- 워커 ----
  public record PendingView(String id,String cls,List<Double> box,String imagePath) {}
  public List<PendingView> pending() {
    var out=new ArrayList<PendingView>();
    for(var p:db.pending(20)) {
      var box=new ArrayList<Double>();
      json.readTree(p.box()).forEach(v->box.add(v.asDouble()));
      out.add(new PendingView(p.id(),p.cls(),box,detections.snapshotFile(p.batchId(),p.capturedAt()).toString()));
    }
    return out;
  }
  public record Embedding(@NotNull @Size(min=DIM,max=DIM) List<Double> vector,@NotBlank @Size(max=64) String model,
      @NotBlank @Size(max=3_000_000) String crop) {}
  public void embed(UUID id,Embedding body) {
    String v=vector(body.vector());
    if(db.item(id.toString())==null) throw error(HttpStatus.NOT_FOUND,"FP_MEMORY_NOT_FOUND");
    writeCrop(id.toString(),body.crop());
    db.embed(id.toString(),v,body.model());
  }

  public record Seed(@NotBlank @Size(max=200) String origin,@NotBlank @Pattern(regexp="hard_hat|safety_vest") String cls,
      @NotBlank @Pattern(regexp="real|false") String label,@NotNull List<Double> box,@Size(max=200) String note,
      @NotNull @Size(min=DIM,max=DIM) List<Double> vector,@NotBlank @Size(max=64) String model,@NotBlank @Size(max=3_000_000) String crop) {}
  public record Saved(String id) {}
  public Saved seed(Seed body) {
    String id=db.upsertClaude(body.origin(),body.cls(),body.label(),box(body.box()),body.note(),vector(body.vector()),body.model());
    writeCrop(id,body.crop());
    return new Saved(id);
  }

  public record Search(@NotBlank @Pattern(regexp="hard_hat|safety_vest") String cls,@NotNull @Size(min=DIM,max=DIM) List<Double> vector,
      @Min(1) @Max(20) Integer k) {}
  public record Found(String id,String source,String label,double similarity,String note,String createdBy,String cropPath) {}
  public List<Found> search(Search body) {
    return db.search(body.cls(),vector(body.vector()),body.k()==null?8:body.k()).stream()
        .map(m->new Found(m.id(),m.source(),m.label(),m.similarity(),m.note(),m.createdBy(),cropFile(m.id()).toString())).toList();
  }

  // ---- 관리자 조회 ----
  public Counts counts() { return db.counts(); }
  public Path crop(UUID id) {
    Path file=cropFile(id.toString());
    if(db.item(id.toString())==null || !Files.isRegularFile(file)) throw error(HttpStatus.NOT_FOUND,"FP_MEMORY_NOT_FOUND");
    return file;
  }

  /** 오탐 확인 행이 지워져 기억도 사라진 확대 사진을 정리한다(1시간마다). */
  @Scheduled(fixedDelay=3_600_000,initialDelay=180_000)
  public void sweep() throws java.io.IOException {
    if(!Files.isDirectory(crops)) return;
    var known=new HashSet<>(db.ids());
    try(var files=Files.list(crops)) {
      for(Path f:files.toList()) {
        String name=f.getFileName().toString();
        if(name.endsWith(".jpg") && !known.contains(name.substring(0,name.length()-4))) Files.deleteIfExists(f);
      }
    }
  }
}
