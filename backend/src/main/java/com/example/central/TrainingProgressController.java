package com.example.central;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@RestController
public class TrainingProgressController {
  private final Path runsRoot;
  private final ObjectMapper json;

  public TrainingProgressController(@Value("${app.training-runs-root:}") String root, ObjectMapper json) {
    this.runsRoot = root.isBlank() ? null : Path.of(root);
    this.json = json;
  }

  /** 실행별 YOLO results.csv(에포크별 손실·지표·학습률). 파일이 아직 없으면 빈 rows. */
  @GetMapping("/api/training/runs/{id}/metrics")
  public Map<String, Object> metrics(@org.springframework.web.bind.annotation.PathVariable java.util.UUID id) throws Exception {
    if (runsRoot == null) return Map.of("columns", List.of(), "rows", List.of());
    Path run = runsRoot.resolve(id.toString()).normalize();
    Path training = run.resolve("training");
    if (!run.startsWith(runsRoot.normalize()) || !Files.isDirectory(training)) return Map.of("columns", List.of(), "rows", List.of());
    Path csv;
    try (Stream<Path> dirs = Files.list(training)) {
      csv = dirs.map(dir -> dir.resolve("results.csv")).filter(Files::isRegularFile).findFirst().orElse(null);
    }
    if (csv == null) return Map.of("columns", List.of(), "rows", List.of());
    List<String> lines = Files.readAllLines(csv).stream().filter(line -> !line.isBlank()).limit(1001).toList();
    if (lines.isEmpty()) return Map.of("columns", List.of(), "rows", List.of());
    List<String> columns = java.util.Arrays.stream(lines.get(0).split(",")).map(String::strip).toList();
    List<List<Double>> rows = new ArrayList<>();
    for (String line : lines.subList(1, lines.size())) {
      String[] values = line.split(",");
      if (values.length != columns.size()) continue;
      List<Double> row = new ArrayList<>();
      try { for (String value : values) row.add(Double.parseDouble(value.strip())); } catch (NumberFormatException e) { continue; }
      rows.add(row);
    }
    return Map.of("columns", columns, "rows", rows);
  }

  /** 실행별 progress.json(현재 단계와 진행 수). 준비 작업 중 외부 라벨 보정·데이터셋 구성을 구분해 보여 준다. */
  @GetMapping("/api/training/runs/{id}/progress")
  public Map<String, Object> runProgress(@org.springframework.web.bind.annotation.PathVariable java.util.UUID id) throws Exception {
    if (runsRoot == null) return Map.of("available", false);
    Path file = runsRoot.resolve(id.toString()).normalize().resolve("progress.json");
    if (!file.startsWith(runsRoot.normalize()) || !Files.isRegularFile(file)) return Map.of("available", false);
    JsonNode data = json.readTree(file.toFile());
    return Map.of("available", true, "step", data.path("step").asText(""), "state", data.path("state").asText(""),
        "current", data.path("current").asInt(0), "total", data.path("total").asInt(0));
  }

  @GetMapping("/api/training/progress")
  public Map<String, Object> progress() {
    if (runsRoot == null || !Files.isDirectory(runsRoot)) return Map.of("available", false);
    try (Stream<Path> runs = Files.list(runsRoot)) {
      Path latest = runs.filter(Files::isDirectory)
          .map(path -> path.resolve("progress.json"))
          .filter(Files::isRegularFile)
          .max(Comparator.comparing(path -> {
            try { return Files.getLastModifiedTime(path).toInstant(); }
            catch (Exception ignored) { return Instant.EPOCH; }
          })).orElse(null);
      if (latest == null) return Map.of("available", false);
      JsonNode data = json.readTree(latest.toFile());
      List<String> completed = new ArrayList<>();
      data.path("completed").forEach(step -> completed.add(step.asText()));
      Path review = latest.getParent().resolve("review.json");
      if (Files.isRegularFile(review) && json.readTree(review.toFile()).path("reviewed").asBoolean(false)
          && !completed.contains("review")) completed.add("review");
      return Map.of("available", true, "run", latest.getParent().getFileName().toString(),
          "step", data.path("step").asText(""), "state", data.path("state").asText(""),
          "current", data.path("current").asInt(0), "total", data.path("total").asInt(0),
          "updatedAt", data.path("updatedAt").asText(""),
          "completed", completed);
    } catch (Exception ignored) {
      return Map.of("available", false);
    }
  }
}
