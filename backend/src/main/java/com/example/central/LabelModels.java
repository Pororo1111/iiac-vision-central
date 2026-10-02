package com.example.central;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public final class LabelModels {
  public record Sample(String id, String sha256, String title, String mime, int width, int height,
      String deviceId, String cameraId, OffsetDateTime capturedAt,
      String status, int revision, String proposal, String annotation, OffsetDateTime createdAt,
      String source, String importId) {}
  public record SamplePage(List<Sample> items,long total,int page,int size) {}
  public record Stats(long total,long unprocessed,long pendingReview,long approved,long excluded,
      long queued,long running,OffsetDateTime lastReceivedAt,long autoQueued,long autoRunning,long labeled) {}
  public record Job(String id, String sampleId, int sampleRevision,
      String instruction, String model, String status, String leaseToken,
      OffsetDateTime leaseUntil, String error, OffsetDateTime createdAt, OffsetDateTime finishedAt,String result,String kind) {}
  public record CreateJob(@NotNull UUID sampleId,@NotBlank @Size(max=2000) String instruction) {}
  public record CreateBatch(@NotNull @Size(min=1,max=40) List<@NotNull UUID> sampleIds,
      @NotBlank @Size(max=2000) String instruction) {}
  public record Box(@NotBlank @Pattern(regexp="person|safety_vest|traffic_cone|hard_hat") String label,
      @DecimalMin("0") @DecimalMax("1") double x,
      @DecimalMin("0") @DecimalMax("1") double y,
      @DecimalMin(value="0",inclusive=false) @DecimalMax("1") double width,
      @DecimalMin(value="0",inclusive=false) @DecimalMax("1") double height) {}
  public record Analysis(@NotBlank @Size(max=8000) String answer,
      @NotNull @Size(max=30) List<@NotBlank @Size(max=80) String> labels, @Size(max=500) List<@NotNull @Valid Box> boxes) {}
  public record Review(@Min(0) int revision,
      @NotNull @Pattern(regexp="REVIEW|APPROVED|EXCLUDED") String status,
      @NotNull @Valid Analysis result) {}
  public record Complete(@NotNull UUID leaseToken, @NotNull @Valid Analysis result,
      @NotBlank @Size(max=200) String modelDigest) {}
  public record Import(String id,String kind,String source,String title,double intervalSeconds,int maxFrames,
      String status,int frames,String leaseToken,String error,OffsetDateTime createdAt,OffsetDateTime finishedAt) {}
  public record YoutubeImport(@NotBlank @Size(max=300) String url,@Size(max=160) String title,
      @DecimalMin("0.5") @DecimalMax("60") double intervalSeconds,@Min(1) @Max(200) int maxFrames) {}
  public record Lease(@NotNull UUID leaseToken) {}
  public record Failure(@NotNull UUID leaseToken, @NotBlank @Size(max=500) String error) {}
}
