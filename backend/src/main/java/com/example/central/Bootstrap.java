package com.example.central;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
public class Bootstrap implements ApplicationRunner {
  private final CentralMapper mapper;
  private final PasswordEncoder encoder;
  private final String username, password, deviceId, deviceToken;

  public Bootstrap(
      CentralMapper mapper,
      PasswordEncoder encoder,
      @Value("${app.admin-username}") String username,
      @Value("${app.admin-password}") String password,
      @Value("${app.device-id}") String deviceId,
      @Value("${app.device-token}") String deviceToken) {
    this.mapper = mapper;
    this.encoder = encoder;
    this.username = username;
    this.password = password;
    this.deviceId = deviceId;
    this.deviceToken = deviceToken;
  }

  public static String hashToken(String token) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(token.getBytes(StandardCharsets.UTF_8)));
    } catch (java.security.NoSuchAlgorithmException e) {
      throw new IllegalStateException(e);
    }
  }

  @Override
  public void run(ApplicationArguments args) {
    if (!username.matches("[A-Za-z0-9_-]{1,64}")
        || password.isBlank()
        || password.getBytes(StandardCharsets.UTF_8).length > 72
        || !deviceId.matches("[A-Za-z0-9_-]{1,64}")
        || deviceToken.length() < 32) {
      throw new IllegalStateException("초기 관리자와 장치 인증 설정을 확인하세요. 비밀번호는 비어 있지 않은 최대 72바이트, 토큰은 32자 이상이어야 합니다.");
    }
    mapper.seedAdmin(username, encoder.encode(password));
    mapper.seedDevice(deviceId, hashToken(deviceToken));
  }
}
