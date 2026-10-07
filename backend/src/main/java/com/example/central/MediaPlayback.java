package com.example.central;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

/** MediaMTX 재생 서버(녹화 목록·클립)와 송출 지연 보정. */
@Component
public class MediaPlayback {
  public record Range(OffsetDateTime start,OffsetDateTime end) {}
  final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
  final ObjectMapper json;
  final String base;
  final long latencyMs;

  public MediaPlayback(ObjectMapper json,@Value("${app.media-playback-url:http://127.0.0.1:9996}") String base,
      @Value("${app.stream-latency-ms:0}") long latencyMs) {
    this.json=json;this.base=base.replaceAll("/+$","");this.latencyMs=latencyMs;
  }

  static String path(String deviceId,String cameraId) {
    return URLEncoder.encode("devices/"+deviceId+"/"+cameraId,StandardCharsets.UTF_8);
  }

  /** 장치 시각 → 녹화 시각(중앙 수신 기준 = 장치 시각 + 송출 지연). */
  OffsetDateTime recordingTime(OffsetDateTime deviceTime) { return deviceTime.plusNanos(latencyMs*1_000_000L); }

  /** 녹화가 있는 구간 목록. 재생 서버에 연결하지 못하거나 녹화가 없으면 빈 목록. */
  public List<Range> list(String deviceId,String cameraId) {
    List<Range> out=new ArrayList<>();
    try {
      var response=http.send(HttpRequest.newBuilder(URI.create(base+"/list?path="+path(deviceId,cameraId)))
          .timeout(Duration.ofSeconds(5)).GET().build(),HttpResponse.BodyHandlers.ofString());
      if(response.statusCode()!=200) return out;
      for(var item:json.readTree(response.body())) {
        var start=OffsetDateTime.parse(item.get("start").asString());
        out.add(new Range(start,start.plusNanos((long)(item.get("duration").asDouble()*1e9))));
      }
    } catch(Exception e) { /* 재생 불가로 본다 */ }
    return out;
  }

  /** 장치 시각 at의 장면이 녹화에 있는지. */
  public boolean covers(List<Range> ranges,OffsetDateTime at) {
    var t=recordingTime(at);
    return ranges.stream().anyMatch(r->!t.isBefore(r.start()) && t.isBefore(r.end()));
  }
}
