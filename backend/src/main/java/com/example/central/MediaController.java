package com.example.central;

import com.example.central.Models.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Pattern;
import java.security.Principal;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

@RestController
@Validated
public class MediaController {
  private final CentralMapper mapper;
  private final MediaService media;

  public MediaController(CentralMapper mapper, MediaService media) {
    this.mapper = mapper;
    this.media = media;
  }

  @PostMapping("/api/iot/cameras/status")
  public Map<String, Boolean> cameraStatus(Principal principal, @Valid @RequestBody CameraStatus status) {
    if (!principal.getName().equals(status.deviceId())) {
      throw new org.springframework.web.server.ResponseStatusException(
          org.springframework.http.HttpStatus.FORBIDDEN, "DEVICE_MISMATCH");
    }
    mapper.upsertCamera(status);
    return Map.of("accepted", true);
  }

  @GetMapping("/api/devices/{deviceId}/cameras")
  public List<DeviceCamera> cameras(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId) {
    return mapper.cameras(deviceId);
  }

  @PostMapping("/api/devices/{deviceId}/cameras/{cameraId}/view-session")
  public ViewSession view(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String deviceId,
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String cameraId) {
    return media.create(deviceId, cameraId);
  }

  @PostMapping("/internal/media/auth")
  public ResponseEntity<Void> authenticate(@RequestBody MediaAuthRequest request) {
    return media.authorize(request)
        ? ResponseEntity.noContent().build()
        : ResponseEntity.status(401).build();
  }
}
