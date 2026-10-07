package com.example.central;

import com.example.central.RecordingMapper.*;
import java.io.InputStream;
import java.nio.file.*;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

/**
 * 원본 녹화 파일을 등록하고 시각을 파일 안의 재생 위치로 바꾼다.
 * 등록은 호스트 워커(워커 Bearer)만 할 수 있고, 재생은 관리자 세션으로 한다.
 */
@Service
public class RecordingService {
  static final long MAX_FILE=500L*1024*1024;
  static final String ID="[A-Za-z0-9_-]{1,64}";
  final RecordingMapper db;
  final Path root;

  public RecordingService(RecordingMapper db,@Value("${app.media-root:/app/data}") String root) {
    this.db=db;this.root=Path.of(root).toAbsolutePath().normalize().resolve("recordings");
  }
  static ResponseStatusException error(HttpStatus status,String code) { return new ResponseStatusException(status,code); }

  public Recording register(MultipartFile file,String deviceId,String cameraId,OffsetDateTime startedAt,double duration) throws Exception {
    return register(file,deviceId,cameraId,startedAt,duration,"FILE");
  }

  /** source: FILE(파일 등록) 또는 STREAM(장치 송출의 MediaMTX 분할 녹화). */
  public Recording register(MultipartFile file,String deviceId,String cameraId,OffsetDateTime startedAt,double duration,String source) throws Exception {
    if(!"FILE".equals(source) && !"STREAM".equals(source)) throw error(HttpStatus.BAD_REQUEST,"INVALID_SOURCE");
    if(deviceId==null || !deviceId.matches(ID) || cameraId==null || !cameraId.matches(ID)) throw error(HttpStatus.BAD_REQUEST,"INVALID_CAMERA");
    if(startedAt==null || !(duration>0 && duration<=86400)) throw error(HttpStatus.BAD_REQUEST,"INVALID_DURATION");
    if(file==null || file.isEmpty() || file.getSize()>MAX_FILE) throw error(HttpStatus.BAD_REQUEST,"INVALID_VIDEO_SIZE");
    // 브라우저 재생을 전제로 MP4(ftyp)만 받는다.
    try(InputStream in=file.getInputStream()) {
      byte[] head=in.readNBytes(8);
      if(head.length<8 || head[4]!='f' || head[5]!='t' || head[6]!='y' || head[7]!='p') throw error(HttpStatus.BAD_REQUEST,"UNSUPPORTED_VIDEO");
    }
    if(!db.deviceExists(deviceId)) throw error(HttpStatus.NOT_FOUND,"DEVICE_NOT_FOUND");
    OffsetDateTime start=startedAt.withOffsetSameInstant(ZoneOffset.UTC).truncatedTo(ChronoUnit.MICROS);
    OffsetDateTime end=start.plusNanos(Math.round(duration*1_000_000)*1000);

    String id=UUID.randomUUID().toString();
    Files.createDirectories(root);
    Path destination=file(id);
    Path temp=Files.createTempFile(root,"upload-",".part");
    String sha;
    try {
      var digest=MessageDigest.getInstance("SHA-256");
      try(InputStream in=new DigestInputStream(file.getInputStream(),digest)) {
        Files.copy(in,temp,StandardCopyOption.REPLACE_EXISTING);
      }
      sha=HexFormat.of().formatHex(digest.digest());
      Files.move(temp,destination,StandardCopyOption.ATOMIC_MOVE);
    } finally { Files.deleteIfExists(temp); }

    String inserted;
    try { inserted=db.insert(id,deviceId,cameraId,start,end,source,sha,file.getSize()); }
    catch(RuntimeException e) { Files.deleteIfExists(destination); throw e; }
    if(inserted!=null) return db.view(id);
    // 같은 파일·길이의 재등록은 기존 녹화를 돌려준다. 내용이 다르면 덮어쓰지 않는다.
    Files.deleteIfExists(destination);
    var existing=db.byStart(deviceId,cameraId,start);
    if(existing!=null && existing.sha256().equals(sha) && existing.endedAt().isEqual(end)) return existing;
    throw error(HttpStatus.CONFLICT,"RECORDING_CONFLICT");
  }

  public List<Recording> list(String deviceId,String cameraId) {
    if((deviceId!=null && !deviceId.matches(ID)) || (cameraId!=null && !cameraId.matches(ID))) throw error(HttpStatus.BAD_REQUEST,"INVALID_CAMERA");
    return db.list(deviceId,cameraId);
  }

  public Recording view(UUID id) {
    var recording=db.view(id.toString());
    if(recording==null) throw error(HttpStatus.NOT_FOUND,"RECORDING_NOT_FOUND");
    return recording;
  }

  public Located locate(String deviceId,String cameraId,OffsetDateTime at) {
    if(deviceId==null || !deviceId.matches(ID) || cameraId==null || !cameraId.matches(ID)) throw error(HttpStatus.BAD_REQUEST,"INVALID_CAMERA");
    if(at==null) throw error(HttpStatus.BAD_REQUEST,"INVALID_REQUEST");
    OffsetDateTime instant=at.withOffsetSameInstant(ZoneOffset.UTC).truncatedTo(ChronoUnit.MICROS);
    var recording=db.covering(deviceId,cameraId,instant);
    if(recording==null) throw error(HttpStatus.NOT_FOUND,"NO_RECORDING");
    double offset=Duration.between(recording.startedAt(),instant).toNanos()/1e9;
    return new Located(recording,offset,"/api/recordings/"+recording.id()+"/video");
  }

  public Path video(UUID id) {
    view(id);
    Path path=file(id.toString());
    if(!Files.isRegularFile(path)) throw error(HttpStatus.NOT_FOUND,"RECORDING_FILE_MISSING");
    return path;
  }

  Path file(String id) { return root.resolve(UUID.fromString(id)+".mp4"); }
}
