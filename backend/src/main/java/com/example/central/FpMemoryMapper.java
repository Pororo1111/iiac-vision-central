package com.example.central;

import java.util.List;
import org.apache.ibatis.annotations.*;

/** 오탐 판단 기억(V37). 임베딩은 pgvector 문자열 표기('[0.1,0.2,...]')로 주고받는다. */
@Mapper
public interface FpMemoryMapper {
  /** 관리자 승인 박스. 같은 박스가 이미 있으면 그대로 둔다. */
  @Insert("""
      INSERT INTO fp_memory(id,source,cls,label,fp_check_id,box_index,box,created_by)
      VALUES(gen_random_uuid(),'ADMIN',#{cls},#{label},#{fpCheckId}::uuid,#{boxIndex},#{box}::jsonb,#{user})
      ON CONFLICT (fp_check_id,box_index) DO NOTHING""")
  void insertAdmin(String fpCheckId,int boxIndex,String cls,String label,String box,String user);

  /** Claude 기본 사례는 임베딩과 함께 들어온다. origin이 같으면 판단·임베딩을 갱신한다. */
  @Select("""
      INSERT INTO fp_memory(id,source,cls,label,box,origin,note,created_by,embedding,embed_model,embedded_at)
      VALUES(gen_random_uuid(),'CLAUDE',#{cls},#{label},#{box}::jsonb,#{origin},#{note},'claude',#{vector}::vector,#{model},now())
      ON CONFLICT (origin) DO UPDATE SET cls=excluded.cls,label=excluded.label,box=excluded.box,note=excluded.note,
        embedding=excluded.embedding,embed_model=excluded.embed_model,embedded_at=now()
      RETURNING id::text""")
  String upsertClaude(String origin,String cls,String label,String box,String note,String vector,String model);

  record Pending(String id,String fpCheckId,String batchId,java.time.OffsetDateTime capturedAt,String cls,String box) {}
  @Select("""
      SELECT m.id::text,m.fp_check_id::text,c.batch_id::text,c.captured_at,m.cls,m.box::text
      FROM fp_memory m JOIN fp_checks c ON c.id=m.fp_check_id
      WHERE m.embedding IS NULL ORDER BY m.created_at,m.id LIMIT #{limit}""")
  List<Pending> pending(int limit);

  @Update("UPDATE fp_memory SET embedding=#{vector}::vector,embed_model=#{model},embedded_at=now() WHERE id=#{id}::uuid")
  int embed(String id,String vector,String model);

  record Match(String id,String source,String cls,String label,double similarity,String note,String createdBy,
      java.time.OffsetDateTime createdAt) {}
  /**
   * 같은 클래스에서 정탐·오탐 판단마다 가장 비슷한 k개(코사인 유사도 순). 한쪽 판단이 많아도 반대 판단의 최근접 사례가
   * 빠지지 않게 판단별로 따로 찾는다(워커는 두 판단의 최고 유사도 차이로 결정한다).
   */
  @Select("""
      SELECT * FROM (
        (SELECT id::text,source,cls,label,1-(embedding<=>#{vector}::vector) AS similarity,note,created_by,created_at
         FROM fp_memory WHERE cls=#{cls} AND label='real' AND embedding IS NOT NULL ORDER BY embedding<=>#{vector}::vector,id LIMIT #{k})
        UNION ALL
        (SELECT id::text,source,cls,label,1-(embedding<=>#{vector}::vector) AS similarity,note,created_by,created_at
         FROM fp_memory WHERE cls=#{cls} AND label='false' AND embedding IS NOT NULL ORDER BY embedding<=>#{vector}::vector,id LIMIT #{k})
      ) m ORDER BY similarity DESC,id""")
  List<Match> search(String cls,String vector,int k);

  @Select("SELECT id::text,source,cls,label,0::float8 AS similarity,note,created_by,created_at FROM fp_memory WHERE id=#{id}::uuid")
  Match item(String id);

  record Counts(int claude,int admin,int pending,int real,int falses) {}
  @Select("""
      SELECT count(*) FILTER (WHERE source='CLAUDE')::int AS claude,count(*) FILTER (WHERE source='ADMIN')::int AS admin,
        count(*) FILTER (WHERE embedding IS NULL)::int AS pending,count(*) FILTER (WHERE label='real')::int AS real,
        count(*) FILTER (WHERE label='false')::int AS falses
      FROM fp_memory""")
  Counts counts();

  @Select("SELECT id::text FROM fp_memory") List<String> ids();
}
