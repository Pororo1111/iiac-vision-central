package com.example.central;

import jakarta.validation.constraints.*;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public final class TrainingModels {
  public record Run(String id,String name,String state,String snapshot,String summary,String automation,OffsetDateTime createdAt,OffsetDateTime updatedAt,
      String baseModel,String external) {}
  public record RunView(String id,String name,String state,String summary,String automation,OffsetDateTime createdAt,OffsetDateTime updatedAt,String baseModel) {
    static RunView of(Run run) { return new RunView(run.id(),run.name(),run.state(),run.summary(),run.automation(),run.createdAt(),run.updatedAt(),run.baseModel()); }
  }
  /** 값이 있으면 구성 완료 뒤 학습·평가·후보 등록·배포 요청을 자동으로 잇는다. deviceId가 없으면 후보 등록에서 멈춘다. */
  public record Auto(@Min(1) @Max(100) int epochs,@Size(max=64) @Pattern(regexp="[A-Za-z0-9._-]+") String deviceId) {}
  public record Task(String id,String runId,String action,String status,String parameters,String leaseToken,String error) {}
  public record Create(@NotBlank @Size(max=100) String name,@NotNull @Size(min=1,max=40) List<@NotNull UUID> sampleIds,
      @DecimalMin("0.5") @DecimalMax("0.95") double confidence,@jakarta.validation.Valid Auto auto,
      @Pattern(regexp=BASE_MODEL) String baseModel) {}
  /** 베이스 모델 키. 임의 파일 경로는 받지 않고 워커가 키를 정해진 경로로 해석한다. */
  public static final String BASE_MODEL="default|yolo26n|yolo26m|release:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  /** kind: SITE(현장 기본)·PRETRAINED(COCO 사전학습)·RELEASE(등록 후보). size: 모델 규모, map: 기준 test mAP50–95(모를 때 null). */
  public record BaseModel(String key,String label,String description,String kind,String size,Double map,OffsetDateTime createdAt) {}
  public record Release(String id,String name,OffsetDateTime createdAt,long sizeBytes,Double map) {}
  public record Train(@Min(1) @Max(100) int epochs) {}
  public record Result(@NotNull UUID leaseToken,@NotBlank @Size(max=100000) String summary) {}
}
