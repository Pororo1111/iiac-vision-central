package com.example.central;

import java.time.OffsetDateTime;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface DeploymentMapper {
  record Model(String id,String sha256,long sizeBytes,OffsetDateTime createdAt) {}
  record Deployment(String id,String modelId,String deviceId,String status,String activeSha256,String message,OffsetDateTime createdAt,OffsetDateTime updatedAt) {}
  /**
   * 현황판 선택용 후보. source는 BASE(현장 기본 모델)·MANUAL(수동 파이프라인)·AUTO(자동 파이프라인)다.
   * 이름은 기본 모델 라벨 또는 파이프라인 이름(없으면 학습 실행 이름), mAP는 평가 test 기준이다.
   */
  record Catalog(String id,String sha256,long sizeBytes,OffsetDateTime createdAt,String name,String source,Double candidateMap,Double baselineMap) {}
  record Policy(String deviceId,String mode,String pinnedModelId,String activeSha256) {}
  record PolicyRow(String deviceId,String name,String modelName,String activeSha256,boolean online,String mode,String pinnedModelId,String pinnedSha256,String note,String updatedBy,OffsetDateTime updatedAt) {}
  record PolicyHistory(long id,String deviceId,String mode,String pinnedModelId,String note,String changedBy,OffsetDateTime changedAt) {}
  String MODEL="id::text,sha256,size_bytes,created_at";
  String DEPLOY="id::text,model_id::text,device_id,status,active_sha256,message,created_at,updated_at";
  @Select("SELECT "+MODEL+" FROM model_releases ORDER BY created_at DESC,id LIMIT 40") List<Model> models();
  @Select("SELECT "+MODEL+" FROM model_releases WHERE id=#{id}::uuid") Model model(String id);
  /** 현황판과 같은 기준의 모델 이름(기본 모델 라벨 → 파이프라인 이름 → 학습 실행 이름). 없으면 null. */
  @Select("SELECT COALESCE(m.label,(SELECT p.name FROM pipelines p WHERE p.run_id=m.id ORDER BY p.created_at DESC LIMIT 1),r.name)"
      + " FROM model_releases m LEFT JOIN training_runs r ON r.id=m.id WHERE m.id=#{id}::uuid") String modelName(String id);
  @Select("SELECT m.id::text,m.sha256,m.size_bytes,m.created_at,COALESCE(m.label,p.name,r.name,'이름 없음') AS name,"
      + " CASE WHEN m.kind='BASE' THEN 'BASE' WHEN p.source_kind='AUTO' THEN 'AUTO' ELSE 'MANUAL' END AS source,"
      + " COALESCE((r.summary#>>'{evaluation,models,candidate,mAP50_95}')::float8,(SELECT (b.summary#>>'{evaluation,models,baseline,mAP50_95}')::float8 FROM training_runs b"
      + "  WHERE b.summary#>>'{evaluation,models,baseline,sha256}'=m.sha256 ORDER BY b.updated_at DESC LIMIT 1)) AS candidate_map,(r.summary#>>'{evaluation,models,baseline,mAP50_95}')::float8 AS baseline_map"
      + " FROM model_releases m LEFT JOIN training_runs r ON r.id=m.id LEFT JOIN LATERAL (SELECT name,source_kind FROM pipelines WHERE run_id=m.id ORDER BY created_at DESC LIMIT 1) p ON true"
      + " ORDER BY m.created_at DESC,m.id LIMIT 100") List<Catalog> catalog();
  /** 장치 행을 잠가 정책 변경과 배포 요청이 서로 엇갈리지 않게 한다. 정책 행이 없으면 PINNED(현재 모델 유지)다. */
  @Select("SELECT d.device_id,COALESCE(p.mode,'PINNED') AS mode,p.pinned_model_id::text,d.active_sha256 FROM devices d"
      + " LEFT JOIN device_model_policies p ON p.device_id=d.device_id WHERE d.device_id=#{device} AND d.registered_at IS NOT NULL FOR UPDATE OF d") Policy lockPolicy(String device);
  @Select("SELECT d.device_id,d.name,d.model_name,d.active_sha256,COALESCE(d.last_seen_at > now() - interval '30 seconds',false) AS online,"
      + " COALESCE(p.mode,'PINNED') AS mode,p.pinned_model_id::text,m.sha256 AS pinned_sha256,COALESCE(p.note,'') AS note,p.updated_by,p.updated_at"
      + " FROM devices d LEFT JOIN device_model_policies p ON p.device_id=d.device_id LEFT JOIN model_releases m ON m.id=p.pinned_model_id"
      + " WHERE d.registered_at IS NOT NULL ORDER BY d.created_at,d.device_id") List<PolicyRow> policies();
  @Insert("INSERT INTO device_model_policies(device_id,mode,pinned_model_id,note,updated_by) VALUES(#{device},#{mode},#{model}::uuid,#{note},#{admin})"
      + " ON CONFLICT(device_id) DO UPDATE SET mode=EXCLUDED.mode,pinned_model_id=EXCLUDED.pinned_model_id,note=EXCLUDED.note,updated_by=EXCLUDED.updated_by,updated_at=now()")
  void savePolicy(String device,String mode,String model,String note,String admin);
  @Insert("INSERT INTO device_model_policy_history(device_id,mode,pinned_model_id,note,changed_by) VALUES(#{device},#{mode},#{model}::uuid,#{note},#{admin})")
  void policyHistory(String device,String mode,String model,String note,String admin);
  @Select("SELECT id,device_id,mode,pinned_model_id::text,note,changed_by,changed_at FROM device_model_policy_history WHERE device_id=#{device} ORDER BY changed_at DESC,id DESC LIMIT 20")
  List<PolicyHistory> history(String device);
  @Select("SELECT DISTINCT ON (device_id) "+DEPLOY+" FROM model_deployments ORDER BY device_id,created_at DESC,id DESC") List<Deployment> latestPerDevice();
  @Insert("INSERT INTO model_releases(id,sha256,size_bytes) VALUES(#{id}::uuid,#{hash},#{size}) ON CONFLICT(id) DO NOTHING") void register(String id,String hash,long size);
  @Insert("INSERT INTO model_releases(id,sha256,size_bytes,kind,label) VALUES(#{id}::uuid,#{hash},#{size},'BASE',#{label})") void registerBase(String id,String hash,long size,String label);
  @Select("SELECT "+MODEL+" FROM model_releases WHERE kind='BASE' AND sha256=#{hash}") Model baseBySha(String hash);
  /** 현장 장치가 보고했거나 평가 기준 모델로 쓰인 해시인지 확인한다. */
  @Select("SELECT EXISTS(SELECT 1 FROM devices WHERE active_sha256=#{hash}) OR EXISTS(SELECT 1 FROM training_runs WHERE summary#>>'{evaluation,models,baseline,sha256}'=#{hash})")
  boolean knownFieldHash(String hash);
  @Select("SELECT "+DEPLOY+" FROM model_deployments ORDER BY created_at DESC,id LIMIT 40") List<Deployment> deployments();
  @Select("SELECT "+DEPLOY+" FROM model_deployments WHERE id=#{id}::uuid") Deployment deployment(String id);
  @Select("SELECT "+DEPLOY+" FROM model_deployments WHERE model_id=#{model}::uuid ORDER BY created_at DESC,id LIMIT 5") List<Deployment> deploymentsFor(String model);
  @Select("SELECT "+DEPLOY+" FROM model_deployments WHERE id=#{id}::uuid FOR UPDATE") Deployment lock(String id);
  @Select("SELECT "+DEPLOY+" FROM model_deployments WHERE device_id=#{device} AND status IN ('PENDING','DOWNLOADING','VALIDATING','APPLYING') ORDER BY created_at,id LIMIT 1") Deployment desired(String device);
  @Insert("INSERT INTO model_deployments(id,model_id,device_id,status) VALUES(#{id}::uuid,#{model}::uuid,#{device},'PENDING')") void insert(String id,String model,String device);
  @Update("UPDATE model_deployments SET status=#{status},active_sha256=#{hash},message=#{message},updated_at=now() WHERE id=#{id}::uuid") void update(String id,String status,String hash,String message);
}
