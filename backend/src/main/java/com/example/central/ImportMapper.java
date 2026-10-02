package com.example.central;

import com.example.central.LabelModels.Import;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface ImportMapper {
  String HEAD="id::text,kind,source,title,interval_seconds,max_frames,status,frames,";
  String TAIL=",error,created_at,finished_at";
  String WORKER=HEAD+"lease_token::text"+TAIL;
  /** 관리자 응답에는 워커 lease 토큰을 싣지 않는다. */
  String ADMIN=HEAD+"null::text AS lease_token"+TAIL;
  @Insert("""
      INSERT INTO media_imports(id,kind,source,title,interval_seconds,max_frames,requested_by)
      VALUES(#{id}::uuid,#{kind},#{source},#{title},#{interval},#{maxFrames},#{user})""")
  void insert(String id,String kind,String source,String title,double interval,int maxFrames,String user);
  @Select("SELECT "+ADMIN+" FROM media_imports ORDER BY created_at DESC,id LIMIT 20") List<Import> recent();
  @Select("SELECT "+ADMIN+" FROM media_imports WHERE id=#{id}::uuid") Import view(String id);
  @Select(value="""
      UPDATE media_imports SET status='RUNNING',lease_token=#{token}::uuid,lease_until=now()+interval '120 seconds'
      WHERE id=(SELECT id FROM media_imports WHERE status='QUEUED' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING
      """+WORKER,affectData=true)
  Import claim(String token);
  @Update("UPDATE media_imports SET lease_until=now()+interval '120 seconds' WHERE id=#{id}::uuid AND status='RUNNING' AND lease_token=#{token}::uuid AND lease_until>now()")
  int heartbeat(String id,String token);
  @Select(value="""
      UPDATE media_imports SET status='FAILED',error='워커 응답 시간 초과. 다시 요청하세요.',finished_at=now(),
        frames=(SELECT count(*) FROM training_samples s WHERE s.import_id=media_imports.id)
      WHERE status='RUNNING' AND lease_until<now() RETURNING id::text""",affectData=true)
  List<String> expire();
  @Select("SELECT count(*) FROM training_samples WHERE import_id=#{id}::uuid") int frameCount(String id);
  @Update("""
      UPDATE media_imports SET status=#{status},error=#{error},finished_at=now(),
        frames=(SELECT count(*) FROM training_samples s WHERE s.import_id=media_imports.id)
      WHERE id=#{id}::uuid""")
  void finish(String id,String status,String error);
}
