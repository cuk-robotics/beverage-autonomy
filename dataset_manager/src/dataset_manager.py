"""Immutable downloads and transactional publication of official LeRobot merges."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from filelock import FileLock

LOG = logging.getLogger(__name__)


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_repo(value: str) -> str:
    if "://" in value:
        parsed = urlparse(value)
        parts = unquote(parsed.path).strip("/").split("/")
        if parsed.scheme != "https" or parsed.netloc != "huggingface.co" or len(parts) != 3 or parts[0] != "datasets":
            raise ValueError("데이터셋 기본 URL(https://huggingface.co/datasets/owner/name)을 입력하세요.")
        value = "/".join(parts[1:])
    if not re.fullmatch(r"[\w-]+/[\w.-]+", value, flags=re.ASCII) or value.split("/")[1] in {".", ".."}:
        raise ValueError(f"잘못된 Hugging Face repo ID: {value}")
    return value


def load_links(path: Path) -> list[str]:
    """Accept a JSON array or an object containing a urls array."""
    with path.open(encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if isinstance(value, dict):
        value = value.get("urls")
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path}: 링크 배열 또는 {{\"urls\": [...]}} 형식의 비어 있지 않은 JSON이 필요합니다.")
    if any(not isinstance(url, str) or not url.strip() for url in value):
        raise ValueError(f"{path}: 모든 링크는 비어 있지 않은 문자열이어야 합니다.")
    return list(dict.fromkeys(parse_repo(url.strip()) for url in value))


def local_file(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    parts = PurePosixPath(relative)
    path = (root / relative).resolve()
    if parts.is_absolute() or ".." in parts.parts or not path.is_relative_to(root.resolve()):
        raise ValueError(f"데이터셋 외부 경로 참조: {relative}")
    if must_exist and not path.is_file():
        raise ValueError(f"참조 파일이 없습니다: {path}")
    return path


def validate_dataset(root: Path, *, deep: bool = False) -> tuple[dict, list[dict]]:
    """Check v3 episode/data/video references; deep checks all frame indices."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq

    info = read_json(root / "meta/info.json")
    if info.get("codebase_version") != "v3.0":
        raise ValueError("현재 LeRobot v3.0만 지원합니다. v2 데이터는 공식 변환 도구로 변환하세요.")
    for key in ("fps", "total_episodes", "total_frames", "chunks_size"):
        if not isinstance(info.get(key), (int, float)) or info[key] <= 0:
            raise ValueError(f"잘못된 meta/info.json 값: {key}")
    features = info.get("features", {})
    if not features or not info.get("data_path"):
        raise ValueError("features 또는 data_path가 없습니다.")
    stats = read_json(root / "meta/stats.json")
    if not stats:
        raise ValueError("빈 통계 메타데이터입니다.")
    tasks = pd.read_parquet(root / "meta/tasks.parquet")
    task_ids = set(int(x) for x in tasks["task_index"])
    if len(tasks) != info["total_tasks"] or task_ids != set(range(len(tasks))) or not tasks.index.is_unique:
        raise ValueError("task 메타데이터가 일치하지 않습니다.")
    episode_files = sorted((root / "meta/episodes").rglob("*.parquet"))
    if not episode_files:
        raise ValueError("meta/episodes Parquet 파일이 없습니다.")
    episodes = pd.concat([pd.read_parquet(p) for p in episode_files], ignore_index=True)
    episodes = episodes.sort_values("episode_index")
    if episodes["episode_index"].tolist() != list(range(info["total_episodes"])):
        raise ValueError("에피소드 인덱스가 연속적이지 않거나 중복되었습니다.")
    video_keys = [k for k, v in features.items() if v["dtype"] == "video"]
    expected_from = 0
    data_ranges = {}
    records = []
    for row in episodes.to_dict("records"):
        start, end, length = (int(row[k]) for k in ("dataset_from_index", "dataset_to_index", "length"))
        if start != expected_from or end - start != length or length <= 0:
            raise ValueError(f"에피소드 프레임 범위 오류: {row['episode_index']}")
        expected_from = end
        data_path = local_file(root, info["data_path"].format(
            chunk_index=int(row["data/chunk_index"]), file_index=int(row["data/file_index"])))
        data_ranges.setdefault(data_path, []).append((start, end, int(row["episode_index"])))
        for camera in video_keys:
            prefix = f"videos/{camera}"
            local_file(root, info["video_path"].format(video_key=camera,
                chunk_index=int(row[f"{prefix}/chunk_index"]), file_index=int(row[f"{prefix}/file_index"])))
            begin, finish = float(row[f"{prefix}/from_timestamp"]), float(row[f"{prefix}/to_timestamp"])
            if not np.isfinite([begin, finish]).all() or begin < 0 or finish <= begin:
                raise ValueError(f"잘못된 영상 시간 범위: {camera}")
        records.append({"episode_index": int(row["episode_index"]), "length": length,
                        "tasks": list(row["tasks"])})
    if expected_from != info["total_frames"]:
        raise ValueError("총 프레임 수 불일치")
    total_rows = 0
    for path, ranges in data_ranges.items():
        count = pq.ParquetFile(path).metadata.num_rows
        total_rows += count
        if count != sum(end - start for start, end, _ in ranges):
            raise ValueError(f"Parquet 행 수 불일치: {path}")
        if deep:
            table = pd.read_parquet(path, columns=["index", "episode_index", "task_index", "frame_index"])
            expected_indices = np.concatenate([np.arange(a, b) for a, b, _ in ranges])
            expected_episodes = np.concatenate([np.full(b - a, ep) for a, b, ep in ranges])
            expected_frames = np.concatenate([np.arange(b - a) for a, b, _ in ranges])
            for column, expected in (("index", expected_indices), ("episode_index", expected_episodes), ("frame_index", expected_frames)):
                if not np.array_equal(table[column].to_numpy(), expected):
                    raise ValueError(f"프레임 {column} 불일치: {path}")
            if not set(table["task_index"].unique()).issubset(task_ids):
                raise ValueError(f"알 수 없는 task_index: {path}")
    if total_rows != info["total_frames"]:
        raise ValueError("실제 프레임 수 불일치")
    return info, records


def inspect_source(root: Path, repo_id: str, commit: str) -> dict:
    info, episodes = validate_dataset(root, deep=True)
    provenance_path = root / "meta/provenance.json"
    provenance = read_json(provenance_path) if provenance_path.exists() else {}
    mapping_path = root / "episode_mapping.csv"
    mapping = {}
    if mapping_path.exists():
        with mapping_path.open(encoding="utf-8-sig", newline="") as handle:
            mapping = {int(r["episode_index"]): r for r in csv.DictReader(handle)}
        if set(mapping) != set(range(info["total_episodes"])):
            raise ValueError("episode_mapping.csv 에피소드 불일치")
    for episode in episodes:
        row = mapping.get(episode["episode_index"], {})
        source_id = row.get("source_dataset_id") or row.get("source_repo_id")
        # A source-local episode number is meaningful only with its source ID.
        # Otherwise use the input dataset's own globally unique episode index.
        source_episode = row.get("source_episode_index") if source_id else None
        episode.update({"source_dataset_id": source_id or repo_id,
                        "source_episode_index": int(source_episode) if source_episode not in (None, "") else episode["episode_index"],
                        "source_revision": row.get("source_revision") if source_id else None,
                        "condition": row.get("condition") or row.get("source_group")})
    readme = next((p for p in (root / "README.md", root / "README.MD") if p.exists()), None)
    return {"repo_id": repo_id, "commit": commit, "root": str(root.resolve()),
            "info": info, "provenance": provenance, "episodes": episodes,
            "card": readme.read_text(encoding="utf-8") if readme else None}


def download_source(repo_id: str, revision: str, storage: Path, workers: int) -> dict:
    from huggingface_hub import HfApi, hf_hub_download

    remote = HfApi().dataset_info(repo_id, revision=revision, files_metadata=True)
    commit = remote.sha
    if not commit or not re.fullmatch("[a-f0-9]{40,64}", commit):
        raise ValueError("유효한 저장소 커밋을 확인할 수 없습니다.")
    root = storage / "sources" / repo_id / commit
    files = remote.siblings or []
    if not files:
        raise ValueError(f"저장소 파일 목록이 비어 있습니다: {repo_id}")
    pending = []
    for file in files:
        path = local_file(root, file.rfilename, must_exist=False)
        if not path.is_file() or (file.size is not None and path.stat().st_size != file.size):
            pending.append(file)
    LOG.info("%s @ %s: 기존 파일 %d개 건너뜀, 다운로드 %d개", repo_id, commit, len(files) - len(pending), len(pending))

    def fetch(file):
        path = local_file(root, file.rfilename, must_exist=False)
        hf_hub_download(repo_id=repo_id, filename=file.rfilename, repo_type="dataset",
                        revision=commit, local_dir=root, force_download=path.exists())

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Consume results so that any download error aborts registration.
            list(pool.map(fetch, pending))
    # Check every remote file, including LFS file sizes, before registering it.
    for file in files:
        path = local_file(root, file.rfilename)
        if file.size is not None and path.stat().st_size != file.size:
            raise ValueError(f"다운로드 크기 불일치: {file.rfilename}")
    return inspect_source(root, repo_id, commit)


def check_compatible(sources: list[dict], allow_overlap: bool) -> None:
    first = sources[0]["info"]
    seen_episodes = set()
    selected = {s["repo_id"] for s in sources}
    for source in sources:
        for key in ("codebase_version", "fps", "robot_type", "features"):
            if source["info"].get(key) != first.get(key):
                raise ValueError(f"병합 불가능: {source['repo_id']}의 {key}가 기존 데이터와 다릅니다.")
        pairs = set(source["provenance"].get("paired_datasets", {}).values())
        if not allow_overlap and len(pairs & selected) > 1:
            raise ValueError("같은 시연의 raw/masked 데이터가 포함되었습니다. 의도한 경우 --allow-overlap 사용")
        for ep in source["episodes"]:
            identity = (ep["source_dataset_id"], ep["source_episode_index"])
            if identity in seen_episodes and not allow_overlap:
                raise ValueError(f"출처가 중복된 에피소드: {identity}. 의도한 경우 --allow-overlap 사용")
            seen_episodes.add(identity)


def normalize_episode_file_references(root: Path, *, repair: bool) -> int:
    """Metadata rows must point to the physical shard containing them."""
    import pandas as pd

    changed = 0
    for path in sorted((root / "meta/episodes").rglob("*.parquet")):
        match = re.fullmatch(r"chunk-(\d+)/file-(\d+)\.parquet", path.relative_to(root / "meta/episodes").as_posix())
        if not match:
            raise ValueError(f"지원하지 않는 에피소드 메타데이터 경로: {path}")
        chunk, file = map(int, match.groups())
        table = pd.read_parquet(path)
        incorrect = False
        for column, expected in (("meta/episodes/chunk_index", chunk), ("meta/episodes/file_index", file)):
            if column not in table or not table[column].eq(expected).all():
                incorrect = True
                table[column] = expected
        if incorrect:
            changed += 1
            if repair:
                table.to_parquet(path, index=False)
    return changed


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def official_merge(sources: list[dict], output: Path, repo_id: str) -> None:
    try:
        from lerobot.datasets.aggregate import aggregate_datasets
    except ImportError as exc:
        raise RuntimeError(
            f"LeRobot 병합 모듈을 불러오지 못했습니다: {exc}\n"
            f'현재 Python: {sys.executable}\n'
            f'설치 명령: "{sys.executable}" -m pip install -r requirements.txt'
        ) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".merge-inputs-", dir=output.parent) as temporary:
        staging = Path(temporary).resolve()
        if staging.parent != output.parent.resolve():
            raise RuntimeError("병합 임시 디렉터리가 예상 경로를 벗어났습니다.")
        roots = []
        for number, source in enumerate(sources):
            root = Path(source["root"])
            affected = normalize_episode_file_references(root, repair=False)
            if affected:
                prepared = staging / str(number)
                # Copy metadata before editing; data/video hard links are read-only inputs.
                shutil.copytree(root / "meta", prepared / "meta")
                for directory in ("data", "videos"):
                    if (root / directory).exists():
                        shutil.copytree(root / directory, prepared / directory, copy_function=link_or_copy)
                normalize_episode_file_references(prepared, repair=True)
                LOG.warning("%s: 메타데이터 파일 %d개의 자기 참조를 병합용 복사본에서 보정 (원본 보존)", source["repo_id"], affected)
                root = prepared
            roots.append(root)
        # Use official no-concatenation options to preserve source video bytes.
        aggregate_datasets(repo_ids=[s["repo_id"] for s in sources],
                           roots=roots, aggr_repo_id=repo_id, aggr_root=output,
                           concatenate_data=False, concatenate_videos=False)
        # Official aggregation may retain source shard offsets in output rows.
        normalize_episode_file_references(output, repair=True)


class DatasetManager:
    def __init__(self, storage: Path):
        self.storage = storage.resolve()
        self.storage.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.storage / "catalog.json"

    def catalog(self) -> dict:
        if not self.catalog_path.exists():
            return {"schema_version": 1, "sources": {}, "merged": None}
        result = read_json(self.catalog_path)
        if result.get("schema_version") != 1:
            raise ValueError("지원하지 않는 catalog schema_version")
        return result

    def add(self, values: list[str], *, revision="main", workers=4,
            allow_overlap=False, repo_id="local/beverage_merged") -> dict:
        repo_ids = list(dict.fromkeys(parse_repo(v) for v in values))
        with FileLock(str(self.storage / ".manager.lock"), timeout=10):
            catalog = self.catalog()
            sources_by_id = dict(catalog["sources"])
            for source_id in repo_ids:
                sources_by_id[source_id] = download_source(source_id, revision, self.storage, workers)
            sources = list(sources_by_id.values())
            check_compatible(sources, allow_overlap)
            identity = [{"repo_id": s["repo_id"], "commit": s["commit"]} for s in sources]
            fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            old = catalog.get("merged")
            if old and old["fingerprint"] == fingerprint and old["repo_id"] == repo_id:
                validate_dataset(Path(old["root"]), deep=True)
                LOG.info("변경 없음: 기존 병합 결과 사용")
                return old
            build_root = self.storage / "merged" / (fingerprint[:16] + "-" + uuid.uuid4().hex[:8])
            build_root.parent.mkdir(parents=True, exist_ok=True)
            try:
                official_merge(sources, build_root, repo_id)
                info, _ = validate_dataset(build_root, deep=True)
                if info["total_episodes"] != sum(s["info"]["total_episodes"] for s in sources) or info["total_frames"] != sum(s["info"]["total_frames"] for s in sources):
                    raise ValueError("병합 결과 총계가 원본 합계와 다릅니다.")
                episode_map = []
                offset = 0
                for source in sources:
                    for ep in source["episodes"]:
                        episode_map.append({**ep, "episode_index": offset + ep["episode_index"],
                            "input_repo_id": source["repo_id"], "input_commit": source["commit"],
                            "input_episode_index": ep["episode_index"]})
                    offset += source["info"]["total_episodes"]
                now = datetime.now(timezone.utc).isoformat()
                write_json(build_root / "meta/collection_provenance.json", {
                    "created_at": now, "sources": identity, "episodes": episode_map,
                    "allow_overlap": allow_overlap,
                    "split_note": "Official merge puts all episodes in train. Create held-out episode splits before evaluation."})
                merged = {"repo_id": repo_id, "root": str(build_root), "fingerprint": fingerprint,
                          "updated_at": now, "total_episodes": info["total_episodes"],
                          "total_frames": info["total_frames"], "total_tasks": info["total_tasks"]}
                # One atomic pointer publishes both the source catalog and the verified merge.
                write_json(self.catalog_path, {"schema_version": 1, "sources": sources_by_id, "merged": merged})
                return merged
            except BaseException:
                # Only this invocation's new build is removed; published generations are retained.
                if build_root.resolve().parent != (self.storage / "merged").resolve():
                    raise RuntimeError("임시 병합 경로가 저장 디렉터리를 벗어났습니다.")
                if build_root.exists():
                    shutil.rmtree(build_root)
                raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Hugging Face LeRobot v3 다운로드 및 공식 규격 병합")
    parser.add_argument("urls", nargs="*", help="데이터셋 URL 또는 owner/name (복수 가능)")
    parser.add_argument("--links-file", "--json", type=Path, action="append", default=[],
                        help="링크 목록 JSON 파일 (여러 번 지정 가능; 직접 입력 URL과 함께 사용 가능)")
    parser.add_argument("--storage", type=Path, default=Path(__file__).resolve().parents[1] / "datasets")
    parser.add_argument("--revision", default="main", help="입력 저장소의 branch/tag/commit")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--repo-id", default="local/beverage_merged", help="로컬 통합 데이터셋 ID; Hub 업로드 없음")
    parser.add_argument("--allow-overlap", action="store_true", help="출처가 같은 시연/paired variant 중복 포함 허용")
    parser.add_argument("--list", action="store_true", help="등록된 원본 및 현재 학습 경로 확인")
    parser.add_argument("--verify", action="store_true", help="현재 통합 데이터셋 전체 인덱스 검사")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.workers < 1:
        parser.error("--workers는 1 이상이어야 합니다.")
    if not args.urls and not args.links_file and not (args.list or args.verify):
        parser.error("데이터셋 URL 또는 --list / --verify를 입력하세요.")
    try:
        urls = list(args.urls)
        for links_file in args.links_file:
            urls.extend(load_links(links_file))
        manager = DatasetManager(args.storage)
        if urls:
            merged = manager.add(urls, revision=args.revision, workers=args.workers,
                                 allow_overlap=args.allow_overlap, repo_id=parse_repo(args.repo_id))
            print(json.dumps(merged, ensure_ascii=False, indent=2))
        if args.list:
            catalog = manager.catalog()
            print(json.dumps({"sources": [{"repo_id": s["repo_id"], "commit": s["commit"],
                "episodes": s["info"]["total_episodes"], "frames": s["info"]["total_frames"]}
                for s in catalog["sources"].values()], "merged": catalog["merged"]}, ensure_ascii=False, indent=2))
        if args.verify:
            merged = manager.catalog()["merged"]
            if not merged:
                raise ValueError("등록된 통합 데이터셋이 없습니다.")
            validate_dataset(Path(merged["root"]), deep=True)
            print("LeRobot v3 metadata/frame reference validation passed.")
        return 0
    except Exception as exc:
        LOG.error("%s", exc)
        return 1
