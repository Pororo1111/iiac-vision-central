"""IoT 테스트 영상 수집·Gemma 자동 분석 조회·기존 박스 데이터 학습 도구.

로컬 개발 전용이다. 실제 웹캠과 현장 모델 설정은 변경하지 않는다.
"""

import argparse
import fcntl
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib import parse, request
import uuid

ROOT = Path(__file__).resolve().parents[1]
IOT = ROOT.parent / "iot-server"
RUNS = ROOT / ".artifacts/training-runs"
sys.path.insert(0, str(ROOT / "scripts"))
from ai_assets import BASE_MODEL, BASELINE, CLASSES, PRETRAINED, compatible  # noqa: E402
PROGRESS = None
PROGRESS_RUN = None
PROGRESS_LOCK = threading.Lock()


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def update_progress(current: int | None = None, total: int | None = None) -> None:
    if PROGRESS_RUN is None or PROGRESS is None:
        return
    with PROGRESS_LOCK:
        if current is not None:
            PROGRESS["current"] = current
        if total is not None:
            PROGRESS["total"] = total
        PROGRESS["updatedAt"] = datetime.now(timezone.utc).isoformat()
        save(PROGRESS_RUN / "progress.json", PROGRESS)


def run_with_progress(run: Path, step: str, action) -> None:
    # CLI와 큐 워커가 같은 호스트 GPU를 동시에 점유하지 않게 한다.
    if step in {"label", "external-label", "dataset", "train", "evaluate"}:
        lock_path = ROOT / ".artifacts/training-execution.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return _run_with_progress(run, step, action)
    return _run_with_progress(run, step, action)


def _run_with_progress(run: Path, step: str, action) -> None:
    global PROGRESS, PROGRESS_RUN
    run.mkdir(parents=True, exist_ok=True)
    progress_file = run / "progress.json"
    previous = json.loads(progress_file.read_text(encoding="utf-8")) if progress_file.exists() else {}
    completed = set(previous.get("completed", []))
    if step in {"dataset", "train"}:
        review_file = run / "review.json"
        if review_file.exists() and json.loads(review_file.read_text(encoding="utf-8")).get("reviewed"):
            completed.add("review")
    PROGRESS_RUN = run
    PROGRESS = {"step": step, "state": "RUNNING", "current": 0, "total": 0,
                "completed": sorted(completed)}
    update_progress()
    stop = threading.Event()
    heartbeat = threading.Thread(target=lambda: heartbeat_progress(stop), daemon=True)
    heartbeat.start()
    try:
        action()
    except BaseException:
        PROGRESS["state"] = "FAILED"
        update_progress()
        raise
    else:
        completed.add(step)
        PROGRESS["completed"] = sorted(completed)
        PROGRESS["state"] = "COMPLETED"
        update_progress()
    finally:
        stop.set()
        heartbeat.join(timeout=1)
        PROGRESS = None
        PROGRESS_RUN = None


def heartbeat_progress(stop: threading.Event) -> None:
    while not stop.wait(5):
        if PROGRESS_RUN is not None and PROGRESS is not None and PROGRESS["step"] == "train":
            results = list((PROGRESS_RUN / "training").glob("*/results.csv"))
            if results:
                completed_epochs = max(0, len(results[-1].read_text(encoding="utf-8").splitlines()) - 1)
                update_progress(completed_epochs)
        update_progress()


def local_sql(statement: str) -> str:
    sys.path.insert(0, str(ROOT / "scripts"))
    from dev import PG, environment
    return subprocess.check_output([str(PG / "psql"), "-v", "ON_ERROR_STOP=1", "-Atc", statement],
                                   env=environment(), text=True).strip()


def provision(run: Path) -> tuple[str, str]:
    secret_file = run / "device-secret.json"
    if secret_file.exists():
        secret = json.loads(secret_file.read_text(encoding="utf-8"))
        return secret["deviceId"], secret["token"]
    device_id = "verify-video-" + uuid.uuid4().hex[:12]
    token = secrets.token_hex(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    local_sql(f"INSERT INTO devices(device_id,name,token_hash) VALUES('{device_id}','테스트 영상 전용','{digest}')")
    temporary = secret_file.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({"deviceId": device_id, "token": token}, output)
    temporary.replace(secret_file)
    return device_id, token


def admin_session(base_url: str):
    sys.path.insert(0, str(ROOT / "scripts"))
    from dev import environment
    env = environment()
    opener = request.build_opener(request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(base_url + "/api/auth/csrf", timeout=20) as response:
        csrf = json.load(response)
    body = parse.urlencode({"username": env["ADMIN_USERNAME"], "password": env["ADMIN_PASSWORD"]}).encode()
    login = request.Request(base_url + "/api/auth/login", body,
                            {"Content-Type": "application/x-www-form-urlencoded",
                             csrf["headerName"]: csrf["token"]}, method="POST")
    with opener.open(login, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("관리자 로그인 실패")
    return opener


def collect(run: Path, base_url: str, frames: int) -> None:
    run_file = run / "run.json"
    if run_file.exists() and json.loads(run_file.read_text(encoding="utf-8")).get("cleanedAt"):
        raise RuntimeError("정리한 작업은 재사용하지 않습니다. 새 작업 디렉터리를 지정하세요.")
    device_id, token = provision(run)
    output = run / "iot-outbox"
    env = dict(os.environ, CENTRAL_DEVICE_TOKEN=token)
    sender = IOT / "scripts/send-test-video.py"
    subprocess.run([str(IOT / ".venv/bin/python"), str(sender), "--video", str(IOT / "assets/test-video.mp4"),
                    "--output", str(output), "--frames", str(frames), "--base-url", base_url,
                    "--device-id", device_id], env=env, check=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    opener = admin_session(base_url)
    originals = run / "central-images"
    originals.mkdir(exist_ok=True)
    for item in manifest["samples"]:
        sample_id = str(uuid.UUID(item["centralId"]))
        with opener.open(base_url + f"/api/training/samples/{sample_id}", timeout=20) as response:
            sample = json.load(response)
        if sample["deviceId"] != device_id or sample["cameraId"] != "test-video":
            raise RuntimeError("중앙 샘플 출처가 일치하지 않습니다.")
        with opener.open(base_url + f"/api/training/samples/{sample_id}/image", timeout=30) as response:
            image = response.read()
        if hashlib.sha256(image).hexdigest() != sample["sha256"]:
            raise RuntimeError("중앙 이미지 해시 불일치")
        (originals / f"{sample_id}.jpg").write_bytes(image)
        update_progress(len(list(originals.glob("*.jpg"))), len(manifest["samples"]))
    save(run / "run.json", {"deviceId": device_id, "sourceKind": "TEST_VIDEO",
                            "videoSha256": manifest["videoSha256"],
                            "classes": CLASSES, "sampleIds": [s["centralId"] for s in manifest["samples"]],
                            "collectedAt": datetime.now(timezone.utc).isoformat()})
    print(f"중앙 수집·재조회 완료: {len(manifest['samples'])}장, 장치 {device_id}")


def collect_webcam(run: Path, base_url: str, device_id: str, camera_id: str, frames: int) -> None:
    """이미 중앙에 수신된 실제 웹캠 원본을 로컬 학습 실행에 복사한다."""
    if (run / "run.json").exists():
        raise RuntimeError("기존 실행의 원본 목록은 덮어쓰지 않습니다. 새 작업 디렉터리를 사용하세요.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", camera_id):
        raise ValueError("장치·카메라 ID 형식 오류")
    opener = admin_session(base_url)
    samples = []
    page = 0
    while len(samples) < frames:
        with opener.open(base_url + f"/api/training/samples?page={page}&deviceId={device_id}", timeout=20) as response:
            result = json.load(response)
        samples.extend(item for item in result["items"] if item["cameraId"] == camera_id)
        page += 1
        if page * result["size"] >= result["total"]:
            break
    samples = samples[:frames]
    if len(samples) < frames:
        raise RuntimeError(f"웹캠 원본이 부족합니다: 요청 {frames}장, 확인 {len(samples)}장")
    originals = run / "central-images"
    originals.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(samples, 1):
        sample_id = str(uuid.UUID(item["id"]))
        with opener.open(base_url + f"/api/training/samples/{sample_id}/image", timeout=30) as response:
            image = response.read()
        if hashlib.sha256(image).hexdigest() != item["sha256"]:
            raise RuntimeError("중앙 이미지 해시 불일치: " + sample_id)
        (originals / f"{sample_id}.jpg").write_bytes(image)
        update_progress(index, len(samples))
    save(run / "run.json", {"deviceId": device_id, "cameraId": camera_id,
                            "sourceKind": "WEBCAM", "classes": CLASSES,
                            "sampleIds": [item["id"] for item in samples],
                            "sourceSha256": hashlib.sha256("\n".join(item["sha256"] for item in samples).encode()).hexdigest(),
                            "collectedAt": datetime.now(timezone.utc).isoformat()})
    print(f"중앙 웹캠 원본 재조회 완료: {len(samples)}장, 장치 {device_id}, 카메라 {camera_id}")


def analyze(run: Path, base_url: str, max_samples: int, wait_seconds: int) -> None:
    source = json.loads((run / "run.json").read_text(encoding="utf-8"))
    ids = source["sampleIds"][:max_samples]
    opener = admin_session(base_url)
    progress = run / "gemma.json"
    state = json.loads(progress.read_text(encoding="utf-8")) if progress.exists() else {"mode": "AUTO_LABEL", "items": {}}
    existing_jobs = state.get("jobs", {})
    # 수집 시 자동 등록된 작업을 기다리며 중복 분석을 요청하지 않는다.
    for sample_id in ids:
        if sample_id in state["items"] or sample_id in existing_jobs:
            continue
        with opener.open(base_url + f"/api/training/label-jobs?sampleId={sample_id}", timeout=20) as response:
            jobs = json.load(response)
        automatic = next((job for job in jobs if job.get("kind") == "AUTO_LABEL"), None)
        if automatic:
            existing_jobs[sample_id] = automatic["id"]
    state["jobs"] = existing_jobs
    save(progress, state)
    pending = [sample_id for sample_id in ids if sample_id not in state["items"] and sample_id not in existing_jobs]
    if pending:
        with opener.open(base_url + "/api/auth/csrf", timeout=20) as response:
            csrf = json.load(response)
        for sample_id in pending:
            req = request.Request(base_url + f"/api/training/samples/{sample_id}/retry-label", b"",
                                  {csrf["headerName"]: csrf["token"]}, method="POST")
            with opener.open(req, timeout=30) as response:
                job = json.load(response)
            state["jobs"][sample_id] = job["id"]
            save(progress, state)
    deadline = time.monotonic() + wait_seconds
    while len([sample_id for sample_id in ids if sample_id in state["items"]]) < len(ids):
        for sample_id in ids:
            if sample_id in state["items"]:
                continue
            with opener.open(base_url + f"/api/training/label-jobs?sampleId={sample_id}", timeout=20) as response:
                jobs = json.load(response)
            job_id = state.get("jobs", {}).get(sample_id)
            job = next((value for value in jobs if value["id"] == job_id), None)
            if job is None:
                raise RuntimeError("Gemma 작업 이력을 찾을 수 없습니다: " + sample_id)
            if job["status"] == "FAILED":
                raise RuntimeError("Gemma 작업 실패: " + sample_id + " / " + str(job.get("error")))
            if job["status"] == "COMPLETED":
                result = json.loads(job["result"])
                state["items"][sample_id] = {"jobId": job_id, "model": job["model"],
                                              "modelDigest": result["modelDigest"],
                                              "result": result["result"]}
                save(progress, state)
                update_progress(len(state["items"]), len(ids))
                print(f"Gemma4 {len(state['items'])}/{len(ids)}: {sample_id}", flush=True)
        if all(sample_id in state["items"] for sample_id in ids):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Gemma 작업 대기 시간이 초과됐습니다. gemma.json과 작업 이력을 확인하세요.")
        time.sleep(5)
    print(f"Gemma4 분석 완료: {len(ids)}장")


def label(run: Path, confidence: float, device: str = "cpu") -> None:
    from pseudo_labels import generate
    source = json.loads((run / "run.json").read_text())
    gemma = json.loads((run / "gemma.json").read_text())
    target = run / "selection.json"
    if target.exists():
        raise RuntimeError("기존 선별 결과는 덮어쓰지 않습니다. 새 실행을 만드세요.")
    selected = generate(run, BASE_MODEL, gemma, source["sampleIds"], confidence, device)
    save(target, selected)
    print(f"자동 선별: {sum(item['include'] for item in selected['items'])}/{len(selected['items'])}장")


def base_weights(run: Path) -> Path:
    """run.json의 베이스 모델 키를 정해진 경로로 해석한다. 키가 없으면 현장 기본 모델이다."""
    source = json.loads((run / "run.json").read_text()) if (run / "run.json").exists() else {}
    key = source.get("baseModel") or "default"
    if key == "default":
        path = BASE_MODEL
    elif key in PRETRAINED:
        path = PRETRAINED[key]
    elif re.fullmatch(r"release:[0-9a-f-]{36}", key):
        path = RUNS / str(uuid.UUID(key[8:])) / "release/model.pt"
    else:
        raise ValueError("알 수 없는 베이스 모델입니다: " + key)
    if not path.is_file():
        raise ValueError("베이스 모델 파일이 없습니다: " + key)
    return path


def expand_classes(base: Path, output: Path) -> Path:
    """앞부분 클래스만 아는 현장 모델(4클래스 등)을 CLASSES 수의 분류 출력으로 넓힌 체크포인트를 만든다.

    ultralytics는 클래스 수가 다르면 분류 출력층을 새로 만들어 기존 클래스 판단까지 처음부터 다시 배운다.
    분류 출력(cv3·one2one_cv3 마지막 층)의 기존 클래스 행은 그대로 옮기고 새 클래스 행만 초기값으로 둔다.
    YOLO26 상자 출력(cv2)은 reg_max=1이라 채널 수가 4로 같을 수 있으므로 이름으로 분류 출력만 고른다.
    이미 CLASSES와 같거나 호환되지 않는(사전학습 COCO) 모델은 그대로 돌려준다.
    """
    import torch
    from ultralytics.nn.tasks import DetectionModel
    checkpoint = torch.load(str(base), map_location="cpu", weights_only=False)
    old = (checkpoint.get("ema") or checkpoint["model"]).float()
    names = [old.names[i] for i in range(len(old.names))]
    if names == CLASSES or not compatible(names):
        return base
    new = DetectionModel(dict(old.yaml), nc=len(CLASSES), verbose=False)
    state, copied = new.state_dict(), 0
    for key, value in old.state_dict().items():
        target = state.get(key)
        if target is None:
            raise RuntimeError("클래스 확장 중 모델 구조가 다릅니다: " + key)
        if target.shape == value.shape:
            state[key] = value
        elif "cv3." in key and value.shape[0] == len(names) and target.shape[1:] == value.shape[1:]:
            target[:len(names)] = value
            copied += 1
        else:
            raise RuntimeError("클래스 확장 중 모델 구조가 다릅니다: " + key)
    if not copied:
        raise RuntimeError("분류 출력층을 찾지 못했습니다.")
    new.load_state_dict(state)
    new.names, new.args = dict(enumerate(CLASSES)), getattr(old, "args", {})
    path = output / "base-expanded.pt"
    torch.save({**checkpoint, "model": new.half(), "ema": None, "optimizer": None, "updates": None, "epoch": -1,
                "best_fitness": None}, path)
    return path


# 외부 데이터가 기준 자료를 덮지 않도록 split별 상한을 둔다. train·val은 기준 자료 수 이하, 전체는 EXTERNAL_LIMIT 이하다.
EXTERNAL_LIMIT = 3000
EXTERNAL_TEST_LIMIT = 200
BATCH = 16
# 기준 자료에 없는 새 클래스(중앙신호등·스마트 콘)는 보강 사진이 수십 장뿐이라 수천 장 사이에서 거의 학습되지 않는다.
# 검토한 local: 데이터셋의 train 이미지 중 새 클래스 박스가 있는 사진을 이 배수만큼 복사해 넣는다(val은 복사하지 않는다).
NEW_CLASS_REPEAT = 10
BASE_CLASS_COUNT = 4


EXTERNAL_LABELS = ROOT / ".artifacts/external-labels"
EXTERNAL_POLICY = "fill-missing-align-hard-hat-v1"


def external_labels(item: dict, device: str, threshold: float) -> tuple[dict, bool]:
    """외부 데이터셋 전체 이미지의 보정 라벨을 만들거나 캐시에서 읽는다. (결과, 캐시 사용 여부)를 돌려준다.

    외부 데이터셋은 보통 일부 클래스만 라벨돼 있다(예: 안전모만). 그대로 쓰면 사진 속 사람을 배경으로 배우므로
    원본에 없는 클래스는 현장 기본 4클래스 모델 박스로 채우고, 애매한 박스가 있는 이미지는 제외한다.
    안전모는 원본이 머리 전체를 감싸는 경우가 있어 기존 모델의 안전모만 감싼 박스로 맞춘다(align_hard_hat).
    데이터셋·매핑·기준 모델·기준값이 같으면 결과가 같으므로 캐시해 실행마다 다시 추론하지 않는다.
    """
    from yolo_datasets import validate_dataset
    from pseudo_labels import align_hard_hat, fill_missing
    record = validate_dataset(Path(item["yamlPath"]))
    mapping = item["mapping"]
    if len(mapping) != len(record["classes"]):
        raise ValueError("외부 데이터셋 클래스가 매핑 이후 바뀌었습니다: " + item["name"])
    images = [(split, image) for split, key in (("train", "train_paths"), ("val", "val_paths"), ("test", "test_paths"))
              for folder in record[key] for image in sorted(Path(folder).iterdir())
              if image.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    key = hashlib.sha256(json.dumps({"policy": EXTERNAL_POLICY, "yaml": item["yamlPath"], "mapping": mapping, "images": len(images),
                                     "model": hashlib.sha256(BASE_MODEL.read_bytes()).hexdigest(), "threshold": threshold},
                                    sort_keys=True).encode()).hexdigest()[:24]
    cache = EXTERNAL_LABELS / (key + ".json")
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8")), True
    from ultralytics import YOLO
    model = YOLO(str(BASE_MODEL))
    if not compatible(model.names[i] for i in range(len(model.names))):
        raise ValueError("기준 모델 클래스 표가 일치하지 않습니다.")
    labeled = {t for t in mapping if t is not None}
    items, excluded = [], {}
    update_progress(0, len(images))
    for start in range(0, len(images), 32):
        chunk = images[start:start + 32]
        results = model.predict([str(image) for _, image in chunk], conf=0.25, imgsz=640, device=device, verbose=False)
        for (split, image), result in zip(chunk, results):
            label = Path(str(image.parent).replace("/images", "/labels")) / (image.stem + ".txt")
            boxes = []
            for line in (label.read_text(encoding="utf-8").splitlines() if label.is_file() else []):
                values = line.split()
                if len(values) < 5 or not values[0].isdigit() or int(values[0]) >= len(mapping):
                    continue
                target = mapping[int(values[0])]
                if target is None:
                    continue
                numbers = [float(v) for v in values[1:]]
                if len(numbers) > 4:
                    # 분할 다각형은 외접 박스로 바꾼다.
                    xs, ys = numbers[0::2], numbers[1::2]
                    numbers = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs), max(ys) - min(ys)]
                boxes.append((target, numbers[:4]))
            detections = [{"label": model.names[int(box.cls.item())], "confidence": float(box.conf.item()),
                           "xywh": box.xywhn[0].tolist()} for box in result.boxes]
            filled = fill_missing(detections, labeled, threshold)
            aligned = align_hard_hat(boxes, detections) if filled["include"] else None
            if aligned is None:
                reason = filled["reason"] if not filled["include"] else "안전모 박스 불일치"
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            items.append({"split": split, "path": str(image),
                          "labels": [f"{t} " + " ".join(f"{v:.6f}" for v in coords) for t, coords in aligned] + filled["labels"]})
        update_progress(start + len(chunk), len(images))
    data = {"policy": EXTERNAL_POLICY, "key": key, "name": item["name"], "total": len(images),
            "included": len(items), "excluded": excluded, "items": items}
    save(cache, data)
    return data, False


def label_external(run: Path, device: str, threshold: float) -> dict:
    """실행에 포함된 외부 데이터셋을 모두 보정하고 요약을 external-labels.json에 남긴다."""
    summary = {}
    for item in json.loads((run / "run.json").read_text()).get("external", []):
        data, cached = external_labels(item, device, threshold)
        summary[item["name"]] = {"total": data["total"], "included": data["included"],
                                 "excluded": sum(data["excluded"].values()), "reasons": data["excluded"], "cached": cached}
    save(run / "external-labels.json", summary)
    return summary


def add_external(run: Path, add, counts: dict, baseline: dict, threshold: float, device: str) -> None:
    """보정된 외부 라벨에서 split별 상한만큼 뽑아 넣는다.

    원본 train·val은 train·val로, 원본 test는 참고 평가용 ext_test로 둔다. 배포 판정 test는 기준 자료만 쓴다.
    후보는 (데이터셋 id, split, 파일명) 해시 순서로 보며 같은 입력은 항상 같은 이미지가 뽑힌다.
    직접 검토한 local: 데이터셋(reviewed)은 소량 보강 자료라 상한에 넣지 않고 전부 먼저 넣는다.
    """
    source = json.loads((run / "run.json").read_text())
    counts["external"] = {}
    candidates = []
    for item in source.get("external", []):
        data, _ = external_labels(item, device, threshold)
        counts["external"][item["name"]] = 0
        entries = [(item, entry, "ext_test" if entry["split"] == "test" else entry["split"]) for entry in data["items"]]
        if item.get("reviewed"):
            for _, entry, split in entries:
                image = Path(entry["path"])
                content = "\n".join(entry["labels"]) + ("\n" if entry["labels"] else "")
                add(image, Path(), split, f"rf_{str(item['id'])[:8]}_{image.name}", content)
                counts["external"][item["name"]] += 1
                if split == "train" and any(int(line.split()[0]) >= BASE_CLASS_COUNT for line in entry["labels"]):
                    for copy in range(1, NEW_CLASS_REPEAT):
                        add(image, Path(), split, f"rf_{str(item['id'])[:8]}_{image.stem}_r{copy}{image.suffix}", content)
                        counts["newClassRepeated"] = counts.get("newClassRepeated", 0) + 1
            continue
        candidates += entries
    limits = {"train": min(EXTERNAL_LIMIT, baseline["train"]), "val": baseline["val"], "ext_test": EXTERNAL_TEST_LIMIT}
    counts["externalAvailable"], counts["externalLimit"] = len(candidates), limits
    candidates.sort(key=lambda c: hashlib.sha256(f"{c[0]['id']}/{c[2]}/{Path(c[1]['path']).name}".encode()).digest())
    added = {split: 0 for split in limits}
    for item, entry, split in candidates:
        if added[split] >= limits[split]:
            continue
        image = Path(entry["path"])
        add(image, Path(), split, f"rf_{str(item['id'])[:8]}_{image.name}", "\n".join(entry["labels"]) + ("\n" if entry["labels"] else ""))
        added[split] += 1
        counts["external"][item["name"]] += 1


INHERIT_SPLITS = ("train", "val", "site_test", "ext_test")


def add_inherited(run: Path, output: Path, add, counts: dict, baseline_names: dict) -> None:
    """베이스로 고른 이전 후보의 스냅샷에서 기준 자료가 아닌 이미지를 같은 split으로 이어받는다.

    이전 스냅샷도 그 부모의 자료를 이어받았으므로 이어학습을 거듭하면 수집·외부 자료가 누적된다.
    기준 test(배포 판정용)는 매번 기준 자료에서 다시 넣으므로 이어받지 않는다. 이번 실행에서 같은 이름으로
    이미 넣은 이미지는 건너뛰며, 이어받은 수는 외부 데이터 상한에 포함하지 않는다.
    """
    key = json.loads((run / "run.json").read_text()).get("baseModel") or "default"
    counts["inherited"] = {}
    if not re.fullmatch(r"release:[0-9a-f-]{36}", key):
        return
    parent = RUNS / str(uuid.UUID(key[8:])) / "combined-dataset"
    if not parent.is_dir():
        # 기본 모델을 등록한 BASE 후보처럼 학습 스냅샷이 없는 후보는 이어받을 자료가 없다.
        return
    counts["inheritedFrom"] = key[8:]
    for split in INHERIT_SPLITS:
        counts["inherited"][split] = 0
        folder = parent / "images" / split
        if not folder.is_dir():
            continue
        for image in sorted(folder.iterdir()):
            if image.name in baseline_names.get(split, set()) or (output / "images" / split / image.name).exists():
                continue
            label = parent / "labels" / split / (image.stem + ".txt")
            if not label.is_file():
                raise RuntimeError("이어받을 라벨 파일이 없습니다: " + str(label))
            add(image, label, split, image.name)
            counts["inherited"][split] += 1


SITE_TEST_MIN = 10


def split_groups(keys: list[str]) -> dict[str, str]:
    """수집 이미지 묶음을 train/val/test 70/15/15로 나눈다.

    같은 영상·업로드 묶음·장치의 10분 구간 프레임은 한쪽에만 두어 비슷한 장면이 train과 test에 함께
    들어가지 않게 한다. 묶음 해시 순서로 정해 같은 입력은 항상 같은 결과가 된다. 묶음이 5개 미만이면 모두 train.
    """
    groups = sorted(set(keys), key=lambda key: hashlib.sha256(key.encode()).digest())
    if len(groups) < 5:
        return {key: "train" for key in groups}
    val = max(1, round(len(groups) * 0.15))
    test = max(1, round(len(groups) * 0.15))
    return {key: "test" if index < test else "val" if index < test + val else "train" for index, key in enumerate(groups)}


def combined_dataset(run: Path, review: dict, baseline_limit: int | None, device: str = "mps") -> Path:
    """기준 자료의 동일 영상 검증 프레임을 제외한 독립 학습 스냅샷."""
    import yaml
    from yolo_datasets import validate_dataset
    output = run / "combined-dataset"
    if output.exists():
        raise RuntimeError("기존 조합 데이터셋이 있습니다. 이전 학습 결과를 보존하기 위해 덮어쓰지 않습니다.")
    source = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))
    if not compatible(source.get("names") or []):
        raise RuntimeError("기준 데이터셋 클래스 표가 일치하지 않습니다.")
    base = Path(source["path"])
    counts = {"train": 0, "val": 0, "test": 0, "site_test": 0, "ext_test": 0, "excludedSameVideo": 0, "videoAdded": 0}

    def add(image: Path, label: Path, split: str, name: str, content: str | None = None) -> None:
        target_image = output / "images" / split / name
        target_label = output / "labels" / split / (Path(name).stem + ".txt")
        target_image.parent.mkdir(parents=True, exist_ok=True)
        target_label.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image, target_image)
        if content is None:
            if label.is_file():
                shutil.copy2(label, target_label)
            else:
                raise RuntimeError("기준 라벨 파일이 없습니다: " + str(label))
        else:
            target_label.write_text(content, encoding="utf-8")
        counts[split] += 1

    baseline_names = {}
    for split in ("train", "val", "test"):
        images = sorted((base / source[split]).iterdir())
        baseline_names[split] = {image.name for image in images}
        if split == "train" and baseline_limit is not None:
            images = sorted(images, key=lambda p: hashlib.sha256(p.name.encode()).digest())[:baseline_limit]
        for image in images:
            if image.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            if "Wle4VAjPWVU" in image.name:
                counts["excludedSameVideo"] += 1
                continue
            label = base / "labels" / split / (image.stem + ".txt")
            add(image, label, split, image.name)
    baseline = {"train": counts["train"], "val": counts["val"]}
    groups = json.loads((run / "run.json").read_text()).get("groups", {})
    included = [item for item in review["items"] if item["include"]]
    splits = split_groups([groups.get(item["sampleId"], item["sampleId"]) for item in included])
    counts["site"] = {"train": 0, "val": 0, "test": 0}
    for item in included:
        sample_id = str(uuid.UUID(item["sampleId"]))
        # 현장 test는 기준 test(배포 판정용)와 섞지 않고 site_test에 따로 둔다.
        split = splits[groups.get(item["sampleId"], item["sampleId"])]
        counts["site"][split] += 1
        image = run / "central-images" / f"{sample_id}.jpg"
        if not image.is_file():
            raise RuntimeError("검토 이미지가 없습니다: " + sample_id)
        lines = item["labels"]
        if not isinstance(lines, list):
            raise ValueError("검토 라벨은 배열이어야 합니다.")
        add(image, Path(), "site_test" if split == "test" else split, "video_" + sample_id + ".jpg",
            "\n".join(lines) + ("\n" if lines else ""))
        counts["videoAdded"] += 1
    add_external(run, add, counts, baseline, review.get("threshold", 0.6), device)
    add_inherited(run, output, add, counts, baseline_names)
    if counts["videoAdded"] < 1 and not any(counts["external"].values()):
        raise RuntimeError("검토를 통과한 영상 프레임이 없습니다.")
    dataset = output / "dataset.yaml"
    dataset.write_text(yaml.safe_dump({"path": str(output), "train": "images/train",
                                       "val": "images/val", "test": "images/test", "names": CLASSES},
                                      allow_unicode=True, sort_keys=False), encoding="utf-8")
    validate_dataset(dataset)
    if counts["site_test"]:
        (output / "site.yaml").write_text(yaml.safe_dump({"path": str(output), "train": "images/train", "val": "images/val",
                                                          "test": "images/site_test", "names": CLASSES},
                                                         allow_unicode=True, sort_keys=False), encoding="utf-8")
    if counts["ext_test"]:
        (output / "external.yaml").write_text(yaml.safe_dump({"path": str(output), "train": "images/train", "val": "images/val",
                                                              "test": "images/ext_test", "names": CLASSES},
                                                             allow_unicode=True, sort_keys=False), encoding="utf-8")
    source = json.loads((run / "run.json").read_text())
    save(output / "provenance.json", {"baseline": str(BASELINE),
                                      "sourceKind": source["sourceKind"],
                                      "sourceSha256": source.get("sourceSha256", source.get("videoSha256")),
                                      "reviewSha256": hashlib.sha256((run / ("selection.json" if (run / "selection.json").exists() else "review.json")).read_bytes()).hexdigest(),
                                      "counts": counts, "baselineLimit": baseline_limit})
    print(f"학습 스냅샷: {counts}", flush=True)
    return dataset


def prepare_dataset(run: Path, baseline_limit: int | None, device: str = "mps") -> Path:
    selection = run / "selection.json"
    review = json.loads((selection if selection.exists() else run / "review.json").read_text(encoding="utf-8"))
    if selection.exists():
        if review.get("policy") != "yolo-gemma-consensus-v1":
            raise RuntimeError("알 수 없는 자동 선별 정책입니다.")
        for item in review["items"]:
            image = run / "central-images" / (str(uuid.UUID(item["sampleId"])) + ".jpg")
            if hashlib.sha256(image.read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError("선별 후 이미지가 변경됐습니다.")
    elif not review.get("reviewed") or not review.get("reviewer"):
        raise RuntimeError("라벨 검토가 필요합니다. review.json에 포함 여부·수정 박스·reviewer를 기록하세요.")
    dataset = run / "combined-dataset/dataset.yaml"
    provenance = run / "combined-dataset/provenance.json"
    if dataset.exists() and provenance.exists():
        previous = json.loads(provenance.read_text(encoding="utf-8"))
        current_hash = hashlib.sha256((run / ("selection.json" if (run / "selection.json").exists() else "review.json")).read_bytes()).hexdigest()
        if previous["reviewSha256"] != current_hash or previous["baselineLimit"] != baseline_limit:
            raise RuntimeError("검토 내용이나 기준 이미지 제한이 바뀌었습니다. 새 작업 디렉터리에서 데이터셋을 다시 구성하세요.")
        from yolo_datasets import validate_dataset
        validate_dataset(dataset)
        return dataset
    return combined_dataset(run, review, baseline_limit, device)


def train(run: Path, epochs: int, device: str, baseline_limit: int | None, learning_rate: float) -> None:
    update_progress(0, epochs)
    combined = prepare_dataset(run, baseline_limit, device)
    from ultralytics import YOLO
    # 박스 초안은 항상 현장 기본 모델로 만들고, 학습은 선택한 베이스 모델에서 시작한다.
    selection_file = run / "selection.json"
    if selection_file.exists() and json.loads(selection_file.read_text())["modelSha256"] != hashlib.sha256(BASE_MODEL.read_bytes()).hexdigest():
        raise RuntimeError("박스 생성 후 기준 모델이 변경됐습니다. 새 실행을 만드세요.")
    base = base_weights(run)
    base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
    model = YOLO(str(expand_classes(base, run)))
    name = "video-" + run.name
    # 이미지 읽기·증강을 별도 프로세스로 돌려 GPU 계산과 겹친다. 실측상 병목은 GPU 계산이라 효과는 몇 % 수준이다.
    workers = min(8, max(1, (os.cpu_count() or 2) // 2))
    # 64GB 통합 메모리에서 YOLO m·640px 배치 8이 약 10GB였다. 16이면 여유가 있고 GPU를 더 꽉 채운다.
    output = model.train(data=str(combined), epochs=epochs, imgsz=640, batch=BATCH, device=device,
                         workers=workers, cache=False, project=str(run / "training"), name=name,
                         exist_ok=False, plots=True, optimizer="AdamW", lr0=learning_rate,
                         warmup_epochs=0, mosaic=0, mixup=0, close_mosaic=0)
    weights = Path(output.save_dir) / "weights/best.pt"
    if not weights.is_file():
        raise RuntimeError("학습 결과 모델이 없습니다.")
    save(run / "model.json", {"weights": str(weights), "sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
                              "baseWeights": str(base), "baseSha256": base_hash, "dataset": str(combined),
                              "epochs": epochs, "device": device, "batch": BATCH, "workers": workers, "baselineLimit": baseline_limit,
                              "learningRate": learning_rate, "classes": list(YOLO(str(weights)).names.values()),
                              "status": "CANDIDATE"})
    print(f"후보 모델: {weights}")


def evaluate(run: Path, device: str) -> None:
    import yaml
    from ultralytics import YOLO
    model_data = json.loads((run / "model.json").read_text(encoding="utf-8"))
    baseline = yaml.safe_load((run / "combined-dataset/dataset.yaml").read_text(encoding="utf-8"))
    if hashlib.sha256(Path(model_data["weights"]).read_bytes()).hexdigest() != model_data["sha256"]:
        raise RuntimeError("학습 후 후보 모델 파일이 변경됐습니다.")
    base = Path(model_data.get("baseWeights", BASE_MODEL))
    if "baseSha256" in model_data and hashlib.sha256(base.read_bytes()).hexdigest() != model_data["baseSha256"]:
        raise RuntimeError("학습 후 베이스 모델 파일이 변경됐습니다.")
    # 선택한 베이스가 현장 클래스 모델(4·6클래스)이면 그보다 나아졌는지, 사전학습(COCO)이면 현장 기본 모델과 비교한다.
    # 기준 test는 앞 4개 클래스만 라벨돼 있어 4클래스 베이스와 6클래스 후보의 mAP가 같은 클래스로 비교된다.
    reference = base if compatible(YOLO(str(base)).names.values()) else BASE_MODEL
    if "test" not in baseline:
        raise RuntimeError("독립 기준 데이터셋의 test split이 없습니다.")
    result = {"evaluationSet": str(run / "combined-dataset/dataset.yaml"), "split": "test", "models": {}}
    result["reference"] = str(reference)
    for name, weights in (("baseline", reference), ("candidate", Path(model_data["weights"]))):
        metrics = YOLO(str(weights)).val(data=str(run / "combined-dataset/dataset.yaml"), split="test", device=device, imgsz=640,
                                         project=str(run / "evaluation"), name=name, plots=True)
        result["models"][name] = {"mAP50": float(metrics.box.map50),
                                    "mAP50_95": float(metrics.box.map),
                                    "precision": float(metrics.box.mp), "recall": float(metrics.box.mr),
                                    "sha256": hashlib.sha256(weights.read_bytes()).hexdigest()}
    baseline_score = result["models"]["baseline"]["mAP50_95"]
    candidate_score = result["models"]["candidate"]["mAP50_95"]
    result["eligibleForPromotion"] = all(math.isfinite(value) for metrics in result["models"].values() for key,value in metrics.items() if key != "sha256") and candidate_score >= baseline_score
    result["deltaMap50_95"] = candidate_score - baseline_score
    # 현장 test는 참고용이다. 라벨이 현장 기본 모델의 박스 초안에서 왔으므로 기본 모델에 유리하게 나올 수 있다.
    site = run / "combined-dataset/site.yaml"
    site_images = len(list((run / "combined-dataset/images/site_test").glob("*"))) if site.exists() else 0
    if site_images >= SITE_TEST_MIN:
        result["site"] = {"images": site_images, "models": {}}
        for name, weights in (("baseline", reference), ("candidate", Path(model_data["weights"]))):
            metrics = YOLO(str(weights)).val(data=str(site), split="test", device=device, imgsz=640,
                                             project=str(run / "evaluation"), name="site-" + name, plots=False)
            result["site"]["models"][name] = {"mAP50": float(metrics.box.map50), "mAP50_95": float(metrics.box.map)}
        result["site"]["deltaMap50_95"] = result["site"]["models"]["candidate"]["mAP50_95"] - result["site"]["models"]["baseline"]["mAP50_95"]
    else:
        result["site"] = {"images": site_images, "skipped": f"현장 test {SITE_TEST_MIN}장 미만"}
    # 외부 test도 참고용이다. 채운 클래스 박스가 기존 모델에서 왔으므로 기존 모델에 유리할 수 있다. 원본 라벨 클래스는 공정하다.
    external = run / "combined-dataset/external.yaml"
    if external.exists():
        result["external"] = {"images": len(list((run / "combined-dataset/images/ext_test").glob("*"))), "models": {}}
        for name, weights in (("baseline", reference), ("candidate", Path(model_data["weights"]))):
            metrics = YOLO(str(weights)).val(data=str(external), split="test", device=device, imgsz=640,
                                             project=str(run / "evaluation"), name="external-" + name, plots=False)
            result["external"]["models"][name] = {"mAP50": float(metrics.box.map50), "mAP50_95": float(metrics.box.map),
                                                  "classes": {CLASSES[int(c)]: float(metrics.box.maps[int(c)]) for c in metrics.box.ap_class_index}}
        result["external"]["deltaMap50_95"] = result["external"]["models"]["candidate"]["mAP50_95"] - result["external"]["models"]["baseline"]["mAP50_95"]
    # 기준 자료에 없는 새 클래스(중앙신호등·스마트 콘)는 val의 보강 데이터로만 참고 성능을 남긴다. 배포 판정에는 쓰지 않는다.
    known = len(YOLO(str(reference)).names)
    if known < len(CLASSES):
        metrics = YOLO(model_data["weights"]).val(data=str(run / "combined-dataset/dataset.yaml"), split="val", device=device, imgsz=640,
                                                  project=str(run / "evaluation"), name="new-classes", plots=False)
        result["newClasses"] = {"split": "val", "classes": {CLASSES[int(c)]: {"mAP50": float(metrics.box.ap50[i]), "mAP50_95": float(metrics.box.ap[i])}
                                                         for i, c in enumerate(metrics.box.ap_class_index) if int(c) >= known}}
    save(run / "evaluation.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cleanup(run: Path) -> None:
    source = json.loads((run / "run.json").read_text(encoding="utf-8"))
    device = source["deviceId"]
    if not re.fullmatch(r"verify-video-[0-9a-f]{12}", device):
        raise RuntimeError("검증 장치만 정리할 수 있습니다.")
    ids = local_sql(f"SELECT id::text FROM training_samples WHERE device_id='{device}'").splitlines()
    local_sql(f"""BEGIN;
      DELETE FROM annotation_reviews WHERE sample_id IN (SELECT id FROM training_samples WHERE device_id='{device}');
      DELETE FROM labeling_jobs WHERE sample_id IN (SELECT id FROM training_samples WHERE device_id='{device}');
      DELETE FROM training_samples WHERE device_id='{device}';
      DELETE FROM events WHERE device_id='{device}';
      DELETE FROM devices WHERE device_id='{device}'; COMMIT;""")
    sys.path.insert(0, str(ROOT / "scripts"))
    from dev import DATA
    for sample_id in ids:
        (DATA / "media" / f"{sample_id}.jpg").unlink(missing_ok=True)
    (run / "device-secret.json").unlink(missing_ok=True)
    source["cleanedAt"] = datetime.now(timezone.utc).isoformat()
    save(run / "run.json", source)
    print(f"중앙 검증 데이터 정리: 장치 {device}, 이미지 {len(ids)}장")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["collect", "collect-webcam", "analyze", "label", "dataset", "train", "evaluate", "cleanup"])
    parser.add_argument("--run", type=Path, required=True, help=".artifacts/training-runs 아래 작업 디렉터리")
    parser.add_argument("--base-url", default="http://127.0.0.1:8200")
    parser.add_argument("--frames", type=int, default=16)
    parser.add_argument("--device-id", default="iot-jetson-001", help="collect-webcam 대상 장치")
    parser.add_argument("--camera-id", default="usb-0", help="collect-webcam 대상 카메라")
    parser.add_argument("--max-samples", type=int, default=40)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    parser.add_argument("--confidence", type=float, default=0.6)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--learning-rate", type=float, default=0.0001)
    parser.add_argument("--baseline-limit", type=int, help="짧은 연결 검증용 기준 train 이미지 수")
    parser.add_argument("--device", default="mps")
    args = parser.parse_args()
    if args.learning_rate <= 0 or args.learning_rate > 0.01:
        parser.error("--learning-rate는 0 초과 0.01 이하여야 합니다.")
    if not 1 <= args.frames <= 40:
        parser.error("--frames는 1~40이어야 합니다.")
    if not 0.25 <= args.confidence <= 1 or not 1 <= args.epochs <= 100 or not 1 <= args.max_samples <= 40:
        parser.error("신뢰도 0.25~1, epoch 1~100, 이미지 1~40 범위를 확인하세요.")
    run = args.run.resolve()
    if not run.is_relative_to(RUNS.resolve()):
        parser.error("--run은 .artifacts/training-runs 아래여야 합니다.")
    def execute() -> None:
        if args.step == "collect":
            collect(run, args.base_url, args.frames)
        elif args.step == "collect-webcam":
            collect_webcam(run, args.base_url, args.device_id, args.camera_id, args.frames)
        elif args.step == "analyze":
            analyze(run, args.base_url, args.max_samples, args.wait_seconds)
        elif args.step == "label":
            label(run, args.confidence, args.device)
        elif args.step == "dataset":
            dataset = prepare_dataset(run, args.baseline_limit, args.device)
            print(f"학습 데이터셋 준비 완료: {dataset}")
        elif args.step == "train":
            train(run, args.epochs, args.device, args.baseline_limit, args.learning_rate)
        elif args.step == "evaluate":
            evaluate(run, args.device)
        else:
            cleanup(run)
    run_with_progress(run, "collect" if args.step == "collect-webcam" else args.step, execute)


if __name__ == "__main__":
    main()
