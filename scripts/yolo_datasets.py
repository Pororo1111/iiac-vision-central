"""YOLO 데이터셋 검증과 Roboflow Universe 다운로드.

sam3-yolo-pipeline 의 dataset_importer 에서 중앙 서버가 쓰는 부분만 가져왔다.
다운로드 위치는 .artifacts/ai-assets/datasets/roboflow 이며 같은 버전은 다시 받지 않는다.
"""

from __future__ import annotations

import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

import yaml

from ai_assets import ROBOFLOW

_IMAGE_SUFFIXES = {
    ".bmp",
    ".dng",
    ".jpeg",
    ".jpg",
    ".mpo",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
_YAML_NAMES = ("data.yaml", "data.yml", "dataset.yaml", "dataset.yml")
_ROBOFLOW_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class DatasetImportError(ValueError):
    """사용자에게 표시할 수 있는 외부 데이터셋 오류."""


def parse_roboflow_universe_url(url: str) -> tuple[str, str, int | None]:
    """Universe 프로젝트 URL에서 workspace, project, 선택 버전을 추출한다."""
    raw_url = str(url or "").strip()
    if not raw_url:
        raise DatasetImportError("Roboflow Universe URL을 입력하세요.")
    if "://" not in raw_url:
        raw_url = f"https://{raw_url}"

    parsed = urlparse(raw_url)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise DatasetImportError("Roboflow Universe URL은 HTTP(S) 주소여야 합니다.")
    if (parsed.hostname or "").casefold() != "universe.roboflow.com":
        raise DatasetImportError(
            "universe.roboflow.com 프로젝트 URL만 사용할 수 있습니다."
        )

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise DatasetImportError(
            "URL에 workspace와 project가 필요합니다. "
            "예: https://universe.roboflow.com/workspace/project"
        )
    workspace, project = parts[:2]
    if not _ROBOFLOW_SLUG_RE.fullmatch(workspace) or not _ROBOFLOW_SLUG_RE.fullmatch(project):
        raise DatasetImportError("Roboflow workspace/project 형식이 올바르지 않습니다.")

    version: int | None = None
    if len(parts) >= 4 and parts[2].casefold() == "dataset":
        try:
            version = int(parts[3])
        except ValueError as exc:
            raise DatasetImportError("Roboflow 데이터셋 버전은 정수여야 합니다.") from exc
        if version <= 0:
            raise DatasetImportError("Roboflow 데이터셋 버전은 1 이상이어야 합니다.")
    return workspace, project, version


def _roboflow_project(api_key: str, workspace: str, project: str):
    try:
        from roboflow import Roboflow
    except ImportError as exc:
        raise DatasetImportError(
            "roboflow 패키지가 설치되지 않았습니다. requirements.txt를 설치하세요."
        ) from exc
    return Roboflow(api_key=api_key).workspace(workspace).project(project)


def _roboflow_version(project: Any, requested: int | None) -> tuple[Any, int]:
    if requested is not None:
        return project.version(requested), requested

    versions = project.versions()
    numbered = []
    for version in versions:
        try:
            numbered.append((int(version.version), version))
        except (AttributeError, TypeError, ValueError):
            continue
    if not numbered:
        raise DatasetImportError("다운로드 가능한 Roboflow 데이터셋 버전이 없습니다.")
    version_number, version = max(numbered, key=lambda item: item[0])
    return version, version_number



def download_roboflow_universe(url: str, api_key: str, model_format: str = "yolo26") -> tuple[dict[str, Any], int]:
    """Universe 데이터셋을 내려받아 검사하고 (첫 데이터셋 정보, 버전)을 돌려준다."""
    workspace, project_slug, requested_version = parse_roboflow_universe_url(url)
    secret = str(api_key or "").strip()
    if not secret:
        raise DatasetImportError(".env 또는 환경변수에 ROBOFLOW_API_KEY를 등록하세요.")

    try:
        project = _roboflow_project(secret, workspace, project_slug)
        version, version_number = _roboflow_version(project, requested_version)
    except DatasetImportError:
        raise
    except Exception as exc:
        message = str(exc).replace(secret, "***")
        raise DatasetImportError(f"Roboflow 프로젝트 조회 실패: {message}") from exc

    root = ROBOFLOW.resolve()
    root.mkdir(parents=True, exist_ok=True)
    base_name = f"{workspace}-{project_slug}-v{version_number}-{model_format}"
    destination = root / base_name

    # 완전히 내려받은 동일 버전은 다시 받지 않는다. 불완전한 동명 폴더가 있으면
    # 보존한 채 새 경로를 사용해 사용자 파일을 덮어쓰지 않는다.
    existing_yamls = discover_dataset_yamls(destination) if destination.is_dir() else []
    if not existing_yamls and destination.exists():
        destination = root / f"{base_name}-{uuid.uuid4().hex[:8]}"

    if not existing_yamls:
        try:
            downloaded = version.download(
                model_format,
                location=str(destination),
                overwrite=False,
            )
            downloaded_location = Path(
                getattr(downloaded, "location", destination)
            ).resolve()
        except Exception as exc:
            message = str(exc).replace(secret, "***")
            raise DatasetImportError(f"Roboflow 데이터셋 다운로드 실패: {message}") from exc
        yaml_paths = discover_dataset_yamls(downloaded_location)
    else:
        yaml_paths = existing_yamls

    if not yaml_paths:
        raise DatasetImportError("다운로드 결과에서 data.yaml을 찾지 못했습니다.")

    errors: list[str] = []
    for yaml_path in yaml_paths:
        try:
            return validate_dataset(yaml_path), version_number
        except DatasetImportError as exc:
            errors.append(f"{yaml_path.name}: {exc}")
    detail = "\n".join(f"- {error}" for error in errors)
    raise DatasetImportError(f"다운로드한 데이터셋 검증에 실패했습니다.\n{detail}")


def _natural_key(value: str) -> list[str | int]:
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    ]


def _read_yaml(yaml_path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DatasetImportError(f"YAML을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(data, dict):
        raise DatasetImportError("데이터셋 YAML의 최상위 값은 매핑이어야 합니다.")
    return data


def _class_names(data: dict[str, Any]) -> list[str]:
    raw_names = data.get("names")
    nc = data.get("nc")

    if isinstance(raw_names, list):
        names = [str(name).strip() for name in raw_names]
    elif isinstance(raw_names, dict):
        try:
            indexed = {int(key): str(value).strip() for key, value in raw_names.items()}
        except (TypeError, ValueError) as exc:
            raise DatasetImportError("names 매핑의 클래스 ID는 정수여야 합니다.") from exc
        expected = list(range(len(indexed)))
        if sorted(indexed) != expected:
            raise DatasetImportError(
                f"names 클래스 ID는 0부터 연속이어야 합니다: {sorted(indexed)}"
            )
        names = [indexed[index] for index in expected]
    elif raw_names is None and isinstance(nc, int) and nc > 0:
        names = [f"class_{index}" for index in range(nc)]
    else:
        raise DatasetImportError("names 또는 양의 정수 nc 항목이 필요합니다.")

    if not names or any(not name for name in names):
        raise DatasetImportError("클래스 이름은 한 개 이상이며 빈 값이 없어야 합니다.")
    if len({name.casefold() for name in names}) != len(names):
        raise DatasetImportError("클래스 이름은 대소문자를 무시했을 때 중복될 수 없습니다.")
    if nc is not None and (not isinstance(nc, int) or nc != len(names)):
        raise DatasetImportError(
            f"nc({nc})와 names 개수({len(names)})가 일치하지 않습니다."
        )
    return names


def _dataset_root(data: dict[str, Any], yaml_path: Path) -> Path:
    raw_root = data.get("path")
    if not raw_root:
        return yaml_path.parent.resolve()

    root = Path(os.path.expandvars(os.path.expanduser(str(raw_root))))
    if not root.is_absolute():
        root = yaml_path.parent / root
    return root.resolve()


def _resolve_split_entry(root: Path, raw_entry: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw_entry.strip()))
    path = Path(expanded)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()

    # Roboflow YAML은 YAML이 데이터셋 루트에 있어도 "../train/images"를
    # 사용하는 경우가 많다. Ultralytics와 동일하게 한 단계 제거 경로도 시도한다.
    if not path.exists() and expanded.replace("\\", "/").startswith("../"):
        path = (root / expanded.replace("\\", "/")[3:]).resolve()
    return path


def _resolve_split(
    data: dict[str, Any],
    yaml_path: Path,
    split: str,
) -> list[Path]:
    raw = data.get(split)
    if split == "val" and raw is None:
        raw = data.get("validation")
    if raw is None:
        raise DatasetImportError(f"YAML에 '{split}' 항목이 없습니다.")

    values = raw if isinstance(raw, list) else [raw]
    if not values or any(not isinstance(value, (str, os.PathLike)) for value in values):
        raise DatasetImportError(f"'{split}'은 경로 문자열 또는 경로 목록이어야 합니다.")

    root = _dataset_root(data, yaml_path)
    paths = [_resolve_split_entry(root, str(value)) for value in values]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        preview = "\n".join(f"- {path}" for path in missing[:3])
        raise DatasetImportError(f"'{split}' 이미지 경로를 찾을 수 없습니다.\n{preview}")
    return paths


def _images_from_path(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(
            (
                item
                for item in path.rglob("*")
                if item.is_file() and item.suffix.casefold() in _IMAGE_SUFFIXES
            ),
            key=lambda item: _natural_key(str(item)),
        )

    if path.suffix.casefold() == ".txt":
        images: list[Path] = []
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            raise DatasetImportError(f"이미지 목록 파일을 읽을 수 없습니다: {path}") from exc
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            image_path = Path(os.path.expandvars(os.path.expanduser(line)))
            if not image_path.is_absolute():
                image_path = path.parent / image_path
            image_path = image_path.resolve()
            if image_path.is_file() and image_path.suffix.casefold() in _IMAGE_SUFFIXES:
                images.append(image_path)
            else:
                raise DatasetImportError(
                    f"이미지 목록에 존재하지 않는 파일이 있습니다: {image_path}"
                )
        return images

    if path.is_file() and path.suffix.casefold() in _IMAGE_SUFFIXES:
        return [path]
    raise DatasetImportError(f"지원하지 않는 이미지 경로입니다: {path}")


def _label_path(image_path: Path) -> Path:
    parts = list(image_path.parts)
    image_indices = [
        index for index, part in enumerate(parts) if part.casefold() == "images"
    ]
    if image_indices:
        parts[image_indices[-1]] = "labels"
        return Path(*parts).with_suffix(".txt")
    return image_path.with_suffix(".txt")


def _validate_labels(images: Iterable[Path], class_count: int) -> tuple[int, int, int]:
    box_count = 0
    missing_count = 0
    empty_count = 0
    errors: list[str] = []
    error_count = 0

    def add_error(message: str) -> None:
        nonlocal error_count
        error_count += 1
        if len(errors) < 10:
            errors.append(message)

    for image_path in images:
        label_path = _label_path(image_path)
        if not label_path.exists():
            missing_count += 1
            continue
        try:
            lines = label_path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            add_error(f"{label_path}: 읽기 실패 ({exc})")
            continue
        nonempty_lines = [line.strip() for line in lines if line.strip()]
        if not nonempty_lines:
            empty_count += 1
            continue

        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            parts = line.split()
            reason = ""
            if len(parts) != 5:
                reason = f"열 {len(parts)}개 (탐지 라벨은 5개 필요)"
            else:
                try:
                    class_value = float(parts[0])
                    coords = [float(value) for value in parts[1:]]
                    values = [class_value, *coords]
                    if not all(math.isfinite(value) for value in values):
                        reason = "NaN 또는 무한대 값"
                    elif not class_value.is_integer():
                        reason = f"정수가 아닌 클래스 ID {parts[0]}"
                    elif not 0 <= int(class_value) < class_count:
                        reason = (
                            f"클래스 ID {int(class_value)}가 허용 범위 "
                            f"0~{class_count - 1} 밖입니다"
                        )
                    elif not all(0.0 <= value <= 1.0 for value in coords):
                        reason = "정규화 좌표가 0~1 범위 밖입니다"
                    elif coords[2] <= 0.0 or coords[3] <= 0.0:
                        reason = "bbox 너비 또는 높이가 0 이하입니다"
                except ValueError:
                    reason = "숫자가 아닌 값"
            if reason:
                add_error(f"{label_path}:{line_number} — {reason}")
            else:
                box_count += 1

    if error_count:
        detail = "\n".join(f"- {error}" for error in errors)
        remainder = error_count - len(errors)
        if remainder > 0:
            detail += f"\n- 그 외 {remainder}개"
        raise DatasetImportError(f"잘못된 YOLO 라벨을 발견했습니다.\n{detail}")
    return box_count, missing_count, empty_count


def validate_dataset(yaml_file: str | os.PathLike[str]) -> dict[str, Any]:
    """YOLO 탐지 데이터셋을 검사하고 UI/학습용 직렬화 가능한 정보를 반환한다."""
    yaml_path = Path(
        os.path.expandvars(os.path.expanduser(str(yaml_file).strip().strip("\"'")))
    )
    if not yaml_path.is_absolute():
        yaml_path = Path.cwd() / yaml_path
    yaml_path = yaml_path.resolve()
    if not yaml_path.is_file():
        raise DatasetImportError(f"데이터셋 YAML을 찾을 수 없습니다: {yaml_path}")

    data = _read_yaml(yaml_path)
    names = _class_names(data)
    train_paths = _resolve_split(data, yaml_path, "train")
    val_paths = _resolve_split(data, yaml_path, "val")
    test_paths = (
        _resolve_split(data, yaml_path, "test") if data.get("test") is not None else []
    )

    split_images: dict[str, list[Path]] = {}
    for split, paths in (
        ("train", train_paths),
        ("val", val_paths),
        ("test", test_paths),
    ):
        images: list[Path] = []
        for path in paths:
            images.extend(_images_from_path(path))
        if split in {"train", "val"} and not images:
            raise DatasetImportError(f"'{split}' 경로에 지원되는 이미지가 없습니다.")
        unique_images = {str(path.resolve()).casefold() for path in images}
        if len(unique_images) != len(images):
            raise DatasetImportError(
                f"'{split}' 이미지 경로 목록에 중복 이미지가 "
                f"{len(images) - len(unique_images):,}장 있습니다."
            )
        split_images[split] = images

    train_set = {str(path.resolve()).casefold() for path in split_images["train"]}
    val_set = {str(path.resolve()).casefold() for path in split_images["val"]}
    overlap = train_set & val_set
    if overlap:
        sample = next(iter(overlap))
        raise DatasetImportError(
            f"train과 val에 같은 이미지가 {len(overlap):,}장 포함되어 있습니다: {sample}"
        )

    label_stats = {
        split: _validate_labels(split_images[split], len(names))
        for split in ("train", "val", "test")
    }
    if label_stats["train"][0] == 0:
        raise DatasetImportError(
            "train 데이터에 유효한 bbox가 없습니다. 빈/누락 라벨만으로는 탐지 모델을 "
            "학습할 수 없습니다."
        )
    box_count = sum(stats[0] for stats in label_stats.values())
    missing_labels = sum(stats[1] for stats in label_stats.values())
    empty_labels = sum(stats[2] for stats in label_stats.values())

    return {
        "id": str(yaml_path).casefold(),
        "name": yaml_path.parent.name,
        "yaml": str(yaml_path),
        "classes": names,
        "train_paths": [path.as_posix() for path in train_paths],
        "val_paths": [path.as_posix() for path in val_paths],
        "test_paths": [path.as_posix() for path in test_paths],
        "train_images": len(split_images["train"]),
        "val_images": len(split_images["val"]),
        "test_images": len(split_images["test"]),
        "boxes": box_count,
        "missing_labels": missing_labels,
        "empty_labels": empty_labels,
    }


def discover_dataset_yamls(folder: str | os.PathLike[str]) -> list[Path]:
    raw_folder = str(folder or "").strip().strip("\"'")
    if not raw_folder:
        raise DatasetImportError("압축 해제된 데이터셋 폴더가 비어 있습니다.")
    root = Path(os.path.expandvars(os.path.expanduser(raw_folder)))
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if not root.is_dir():
        raise DatasetImportError(f"압축 해제된 데이터셋 폴더를 찾을 수 없습니다: {root}")

    direct = [root / name for name in _YAML_NAMES if (root / name).is_file()]
    candidates = direct or [
        path
        for name in _YAML_NAMES
        for path in root.rglob(name)
        if path.is_file()
    ]
    unique = sorted({path.resolve() for path in candidates}, key=lambda p: str(p))
    if not unique:
        raise DatasetImportError(
            "폴더에서 data.yaml 또는 dataset.yaml을 찾을 수 없습니다."
        )
    return unique


