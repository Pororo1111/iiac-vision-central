package com.example.central;

import com.example.central.Models.*;
import java.security.SecureRandom;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
public class MediaService {
  private record Grant(String path, OffsetDateTime expiresAt) {}

  private final CentralMapper mapper;
  private final String publicUrl;
  private final SecureRandom random = new SecureRandom();
  private final ConcurrentHashMap<String, Grant> grants = new ConcurrentHashMap<>();

  public MediaService(CentralMapper mapper, @Value("${app.media-public-url}") String publicUrl) {
    this.mapper = mapper;
    this.publicUrl = publicUrl.replaceAll("/+$", "");
  }

  public ViewSession create(String deviceId, String cameraId) {
    var device = mapper.device(deviceId);
    var camera = mapper.camera(deviceId, cameraId);
    if (device == null || camera == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "CAMERA_NOT_FOUND");
    if (!device.online()) throw new ResponseStatusException(HttpStatus.CONFLICT, "DEVICE_OFFLINE");
    if (!"STREAMING".equals(camera.streamStatus())) {
      throw new ResponseStatusException(HttpStatus.CONFLICT, "STREAM_NOT_READY");
    }
    purge();
    byte[] bytes = new byte[32];
    random.nextBytes(bytes);
    String token = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    String path = streamPath(deviceId, cameraId);
    OffsetDateTime expires = OffsetDateTime.now(ZoneOffset.UTC).plusMinutes(5);
    grants.put(token, new Grant(path, expires));
    return new ViewSession(publicUrl + "/" + path + "/whep", token, expires);
  }

  public boolean authorize(MediaAuthRequest request) {
    if (request == null || request.action() == null || request.path() == null) return false;
    if ("publish".equals(request.action())) return authorizePublisher(request);
    if (!"read".equals(request.action()) || request.token() == null) return false;
    var grant = grants.get(request.token());
    if (grant == null || grant.expiresAt().isBefore(OffsetDateTime.now(ZoneOffset.UTC))) {
      grants.remove(request.token());
      return false;
    }
    return grant.path().equals(request.path());
  }

  private boolean authorizePublisher(MediaAuthRequest request) {
    if (request.user() == null || request.password() == null || request.user().length() > 64) return false;
    String owner = mapper.deviceByToken(Bootstrap.hashToken(request.password()));
    if (!request.user().equals(owner)) return false;
    String prefix = "devices/" + owner + "/";
    return request.path().startsWith(prefix)
        && request.path().length() > prefix.length()
        && request.path().substring(prefix.length()).matches("[A-Za-z0-9_-]{1,64}");
  }

  static String streamPath(String deviceId, String cameraId) {
    if (!deviceId.matches("[A-Za-z0-9_-]{1,64}") || !cameraId.matches("[A-Za-z0-9_-]{1,64}")) {
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "INVALID_CAMERA");
    }
    return "devices/" + deviceId + "/" + cameraId;
  }

  private void purge() {
    var now = OffsetDateTime.now(ZoneOffset.UTC);
    grants.entrySet().removeIf(entry -> entry.getValue().expiresAt().isBefore(now));
  }
}
