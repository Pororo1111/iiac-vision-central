package com.example.central;

import java.nio.file.*;
import java.util.List;
import java.util.UUID;
import org.apache.ibatis.annotations.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

@Component
public class MediaCleanup implements ApplicationRunner {
  @Mapper
  public interface CleanupMapper {
    @Select("SELECT sample_id::text FROM pending_media_deletions") List<String> pending();
    @Delete("DELETE FROM pending_media_deletions WHERE sample_id=#{id}::uuid") void removed(String id);
  }
  private final CleanupMapper db;
  private final Path root;
  public MediaCleanup(CleanupMapper db,@Value("${app.media-root}") String root) {
    this.db=db; this.root=Path.of(root);
  }
  public void run(ApplicationArguments args) throws Exception {
    for(String id:db.pending()) {
      Files.deleteIfExists(root.resolve(UUID.fromString(id)+".jpg"));
      db.removed(id);
    }
  }
}
