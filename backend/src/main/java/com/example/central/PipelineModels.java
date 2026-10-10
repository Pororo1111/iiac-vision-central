package com.example.central;

import static com.example.central.TrainingModels.BASE_MODEL;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public final class PipelineModels {
  /** 관리자 응답용 외부 데이터셋. 로컬 경로(yaml_path)는 싣지 않는다. */
  public record External(String id,String url,String workspace,String project,Integer version,String status,String name,
      String classes,String mapping,String counts,String leaseToken,String error,OffsetDateTime createdAt,OffsetDateTime finishedAt,
      String phase,Integer progress) {}
  public record ExternalBeat(@NotNull UUID leaseToken,@Pattern(regexp="EXPORT|DOWNLOAD|EXTRACT|VALIDATE") String phase,@Min(0) @Max(100) Integer progress) {}
  public record ExternalRequest(@NotBlank @Size(max=300) String url) {}
  /** mapping[i]는 원본 클래스 i를 CLASSES 번호(0~5)로 바꾼 값이며 null은 버림이다. */
  public record ExternalComplete(@NotNull UUID leaseToken,@NotBlank @Size(max=200) String name,@Min(1) int version,
      @NotBlank @Size(max=500) String yamlPath,@NotNull @Size(min=1,max=200) List<@NotNull @Size(max=100) String> classes,
      @NotNull @Size(min=1,max=200) List<@Min(0) @Max(5) Integer> mapping,@NotBlank @Size(max=2000) String counts) {}
  /** 선택한 외부 데이터셋은 train·val 전체를 사용한다. */
  public record ExternalChoice(@NotNull UUID id) {}

  public record Source(@Size(max=10) List<@NotBlank @Size(max=64) String> deviceIds,@Min(1) @Max(200) Integer targetCount,
      Boolean includeExisting,@Size(max=200) List<@NotNull UUID> sampleIds,@Size(max=20) List<@NotNull UUID> importIds) {}
  public record Options(@NotNull @Pattern(regexp=BASE_MODEL) String baseModel,@Min(1) @Max(100) int epochs,
      @DecimalMin("0.5") @DecimalMax("0.95") double confidence) {}
  /** name은 쓰지 않는다. 학습 후 Gemma가 이름을 추천한다. */
  public record Create(@Size(max=100) String name,@NotNull @Pattern(regexp="IOT|UPLOAD|EXISTING|ROBOFLOW") String sourceKind,
      @NotNull @Valid Source source,@NotNull @Size(max=30) List<@NotNull @Valid ExternalChoice> external,@NotNull @Valid Options options) {}

  public record Pipeline(String id,String name,String sourceKind,String source,String external,String options,String stage,String runId,
      int sampleCount,int labeledCount,String message,String createdBy,OffsetDateTime createdAt,OffsetDateTime updatedAt,String nameStatus) {}
  /** 워커에 넘기는 이름 추천 요청. context는 데이터 구성·옵션·결과 요약 JSON이다. */
  public record NamingJob(String id,String context) {}
  public record NameResult(@NotBlank @Size(max=60) String name) {}
  public record Member(String id,String status,boolean busy) {}
  public record AutoSettings(boolean enabled,java.time.LocalTime runAt,int minSamples,int epochs,String basePolicy,
      java.time.LocalDate lastRunDate,String lastResult,OffsetDateTime updatedAt) {}
  public record AutoUpdate(boolean enabled,@NotNull @Pattern(regexp="([01]\\d|2[0-3]):[0-5]\\d") String runAt,
      @Min(1) @Max(1000) int minSamples,@Min(1) @Max(100) int epochs,@NotNull @Pattern(regexp="LATEST|DEFAULT") String basePolicy) {}
  /** 자동 파이프라인 화면용 상태. */
  public record AutoStatus(AutoSettings settings,int pending,long labelingQueued,long discardedToday,int devices,int onlineDevices,
      OffsetDateTime nextRunAt,String latestPipelineId) {}
  public record View(Pipeline pipeline,TrainingModels.RunView run,DeploymentMapper.Model release,List<DeploymentMapper.Deployment> deployments) {}
}
