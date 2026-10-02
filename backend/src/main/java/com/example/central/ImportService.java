package com.example.central;

import com.example.central.LabelModels.*;
import java.io.InputStream;
import java.net.URI;
import java.nio.file.*;
import java.util.*;
import java.util.regex.Pattern;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

/**
 * 관리자 수동 입력(이미지·영상 파일·유튜브 링크)을 수집 이미지로 바꾼다.
 * 이미지는 바로 저장하고, 영상은 작업으로 등록해 호스트 AI 워커가 프레임을 추출·전송한다.
 * 저장된 프레임은 IoT 수집 이미지와 같은 자동 라벨링 대기열에 들어간다.
 */
@Service
public class ImportService {
  static final long MAX_VIDEO=500L*1024*1024;
  static final Pattern VIDEO_ID=Pattern.compile("[A-Za-z0-9_-]{11}");
  static final Set<String> EXTENSIONS=Set.of("mp4","mov","m4v","webm","mkv","avi");
  final ImportMapper db;
  final LabelService labels;
  final Path videos;
  public ImportService(ImportMapper db,LabelService labels,@Value("${app.media-root:/app/data}") String root) {
    this.db=db;this.labels=labels;this.videos=Path.of(root).toAbsolutePath().normalize().resolve("imports");
  }
  static ResponseStatusException bad(String code) { return new ResponseStatusException(HttpStatus.BAD_REQUEST,code); }
  static String clip(String value,int max) { value=value.strip(); return value.length()>max?value.substring(0,max):value; }

  public List<Sample> uploadImages(List<MultipartFile> files) throws Exception {
    if(files==null || files.isEmpty() || files.size()>20) throw bad("INVALID_FILE_COUNT");
    var result=new ArrayList<Sample>();
    for(var file:files) {
      String name=file.getOriginalFilename()==null||file.getOriginalFilename().isBlank()?"업로드 이미지":file.getOriginalFilename();
      result.add(labels.uploadManual(file,clip(Path.of(name).getFileName().toString(),160)));
    }
    return result;
  }

  public Import createVideo(MultipartFile file,String title,double interval,int maxFrames,String user) throws Exception {
    validate(interval,maxFrames);
    if(file==null || file.isEmpty() || file.getSize()>MAX_VIDEO) throw bad("INVALID_VIDEO_SIZE");
    String name=Path.of(Objects.requireNonNullElse(file.getOriginalFilename(),"video")).getFileName().toString();
    int dot=name.lastIndexOf('.');
    if(dot<0 || !EXTENSIONS.contains(name.substring(dot+1).toLowerCase(Locale.ROOT))) throw bad("UNSUPPORTED_VIDEO");
    try(InputStream in=file.getInputStream()) { if(!videoSignature(in.readNBytes(12))) throw bad("UNSUPPORTED_VIDEO"); }
    String id=UUID.randomUUID().toString();
    Files.createDirectories(videos);
    Path destination=video(id);
    file.transferTo(destination);
    try {
      db.insert(id,"VIDEO_FILE",clip(name,300),title(title,name.substring(0,dot)),interval,maxFrames,user);
    } catch(RuntimeException e) { Files.deleteIfExists(destination); throw e; }
    return db.view(id);
  }

  /** MP4·MOV(ftyp), WebM·MKV(EBML), AVI(RIFF) 머리글만 허용한다. 실제 디코딩 가능 여부는 워커가 확인한다. */
  static boolean videoSignature(byte[] head) {
    if(head.length<12) return false;
    if(head[4]=='f' && head[5]=='t' && head[6]=='y' && head[7]=='p') return true;
    if((head[0]&0xff)==0x1A && (head[1]&0xff)==0x45 && (head[2]&0xff)==0xDF && (head[3]&0xff)==0xA3) return true;
    return head[0]=='R' && head[1]=='I' && head[2]=='F' && head[3]=='F' && head[8]=='A' && head[9]=='V' && head[10]=='I';
  }

  public Import createYoutube(YoutubeImport request,String user) {
    validate(request.intervalSeconds(),request.maxFrames());
    String videoId=youtubeId(request.url());
    // 재생목록·시작 시각 등 부가 파라미터는 버리고 단일 영상 주소로 정규화해 저장한다.
    String canonical="https://www.youtube.com/watch?v="+videoId;
    String id=UUID.randomUUID().toString();
    db.insert(id,"YOUTUBE",canonical,title(request.title(),"유튜브 "+videoId),request.intervalSeconds(),request.maxFrames(),user);
    return db.view(id);
  }

  static String youtubeId(String url) {
    URI uri;
    try { uri=URI.create(url.strip()); } catch(IllegalArgumentException e) { throw bad("INVALID_YOUTUBE_URL"); }
    String host=uri.getHost()==null?"":uri.getHost().toLowerCase(Locale.ROOT);
    String path=uri.getPath()==null?"":uri.getPath();
    String id=null;
    if(!"https".equalsIgnoreCase(uri.getScheme()) && !"http".equalsIgnoreCase(uri.getScheme())) throw bad("INVALID_YOUTUBE_URL");
    if(host.equals("youtu.be")) id=path.replaceFirst("^/","");
    else if(host.equals("youtube.com") || host.equals("www.youtube.com") || host.equals("m.youtube.com")) {
      if(path.equals("/watch") && uri.getRawQuery()!=null)
        for(String pair:uri.getRawQuery().split("&")) if(pair.startsWith("v=")) id=pair.substring(2);
      if(path.startsWith("/shorts/")) id=path.substring(8);
    }
    if(id==null || !VIDEO_ID.matcher(id).matches()) throw bad("INVALID_YOUTUBE_URL");
    return id;
  }

  static void validate(double interval,int maxFrames) {
    if(!(interval>=0.5 && interval<=60) || maxFrames<1 || maxFrames>200) throw bad("INVALID_IMPORT_OPTIONS");
  }
  static String title(String requested,String fallback) {
    String value=requested==null||requested.isBlank()?fallback:requested;
    return clip(value.isBlank()?"영상 가져오기":value,120);
  }
  Path video(String id) { return videos.resolve(UUID.fromString(id)+".video"); }

  public List<Import> recent() { expire(); return db.recent(); }
  public Import claim() { expire(); return db.claim(UUID.randomUUID().toString()); }
  public void heartbeat(String id,String lease) { LabelService.require(db.heartbeat(id,lease)==1,"LEASE_EXPIRED"); }
  public Path videoFile(String id) {
    Path path=video(id);
    if(!Files.exists(path)) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"VIDEO_NOT_FOUND");
    return path;
  }
  public Sample frame(String id,String lease,double position,MultipartFile file) throws Exception {
    heartbeat(id,lease);
    var job=db.view(id);
    if(!(position>=0 && position<=86400)) throw bad("INVALID_POSITION");
    LabelService.require(db.frameCount(id)<job.maxFrames(),"FRAME_LIMIT");
    long seconds=(long)position;
    String at=seconds>=3600?"%d:%02d:%02d".formatted(seconds/3600,seconds/60%60,seconds%60):"%02d:%02d".formatted(seconds/60,seconds%60);
    return labels.uploadFrame(file,job.title()+" · "+at,job.kind(),id);
  }
  public void complete(String id,String lease) throws Exception {
    heartbeat(id,lease);
    db.finish(id,"COMPLETED",null);
    Files.deleteIfExists(video(id));
  }
  public void fail(String id,String lease,String error) throws Exception {
    heartbeat(id,lease);
    db.finish(id,"FAILED",error);
    Files.deleteIfExists(video(id));
  }
  void expire() {
    for(String id:db.expire()) {
      try { Files.deleteIfExists(video(id)); } catch(Exception ignored) { }
    }
  }
}
