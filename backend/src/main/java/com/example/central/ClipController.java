package com.example.central;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.Resource;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

/**
 * 장치 송출 녹화의 클립 재생. MediaMTX 재생 서버가 [at−before, at+after] 구간을 1분 분할 파일에 걸쳐 이어 붙인
 * 일반 MP4로 만들고(fMP4는 전체 길이가 0으로 기록되어 iOS Safari 등이 첫 조각 약 1초만 영상 길이로 본다), 중앙은 관리자 세션을 확인한 뒤 메모리에 받아 Range 요청을 지원하는 응답으로 전달한다(최대 2분).
 */
@RestController
public class ClipController {
  static final String ID="[A-Za-z0-9_-]{1,64}";
  static final int MAX_SECONDS=120;
  final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
  final String playback;
  final long latencyMs;

  final MediaPlayback media;

  public ClipController(MediaPlayback media) {
    this.media=media;this.playback=media.base;this.latencyMs=media.latencyMs;
  }

  @GetMapping("/api/clips")
  public ResponseEntity<Resource> clip(@RequestParam String deviceId,@RequestParam String cameraId,
      @RequestParam @DateTimeFormat(iso=DateTimeFormat.ISO.DATE_TIME) OffsetDateTime at,
      @RequestParam(defaultValue="10") double before,@RequestParam(defaultValue="20") double after) throws Exception {
    if(!deviceId.matches(ID) || !cameraId.matches(ID) || before<0 || after<=0 || before+after>MAX_SECONDS)
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"INVALID_CLIP");
    // 녹화 시각(중앙 수신 기준) = 장치 시각 + 송출 지연
    OffsetDateTime start=at.withOffsetSameInstant(ZoneOffset.UTC).plusNanos(latencyMs*1_000_000L-(long)(before*1e9));
    double seconds=before+after;
    // 요청 시작이 녹화 범위 앞이면(송출 시작 직후 장면) MediaMTX가 404를 준다. 장면이 든 녹화 범위의 시작으로 당긴다.
    var scene=media.recordingTime(at.withOffsetSameInstant(ZoneOffset.UTC));
    var range=media.list(deviceId,cameraId).stream()
        .filter(r->!scene.isBefore(r.start()) && scene.isBefore(r.end())).findFirst().orElse(null);
    if(range==null) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"CLIP_NOT_FOUND");
    if(start.isBefore(range.start())) {
      seconds-=Duration.between(start,range.start()).toNanos()/1e9;
      start=range.start().withOffsetSameInstant(ZoneOffset.UTC);
    }
    String query="path="+URLEncoder.encode("devices/"+deviceId+"/"+cameraId,StandardCharsets.UTF_8)
        +"&start="+URLEncoder.encode(start.format(DateTimeFormatter.ISO_OFFSET_DATE_TIME),StandardCharsets.UTF_8)
        +"&duration="+String.format(java.util.Locale.ROOT,"%.3f",seconds)+"&format=mp4";
    HttpResponse<byte[]> response;
    try {
      response=http.send(HttpRequest.newBuilder(URI.create(playback+"/get?"+query)).timeout(Duration.ofSeconds(20)).GET().build(),
          HttpResponse.BodyHandlers.ofByteArray());
    } catch(java.io.IOException e) {
      throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,"PLAYBACK_UNAVAILABLE");
    }
    if(response.statusCode()==404 || response.statusCode()==400 && response.body().length<1000)
      throw new ResponseStatusException(HttpStatus.NOT_FOUND,"CLIP_NOT_FOUND");
    if(response.statusCode()!=200 || response.body().length==0)
      throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,"PLAYBACK_FAILED");
    return ResponseEntity.ok().contentType(MediaType.parseMediaType("video/mp4"))
        .cacheControl(CacheControl.noStore().cachePrivate()).body(new ByteArrayResource(response.body()));
  }
}
