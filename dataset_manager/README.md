# LeRobot Dataset Manager

Hugging Face의 LeRobot v3 데이터셋을 다운로드하고 공식 LeRobot 병합 API로 통합하는 CLI입니다.
여러 데이터셋을 한 번에 지정하거나, 나중에 추가한 데이터셋까지 누적해서 다시 병합할 수 있습니다.

## 파일 구성

```text
datasets_manager.py         # CLI 실행 파일
src/
  __init__.py
  dataset_manager.py        # 다운로드·병합·검증 구현
configs/
  dataset_links.json        # 예제 데이터셋 URL 목록
requirements.txt
.gitignore
README.md
```

이 폴더 자체를 저장소 루트로 사용합니다. 원래 학습 프로젝트의 다른 파일은 필요하지 않습니다.

## 설치

Python **3.12 이상**이 필요합니다(LeRobot 0.6.1의 요구사항).
다음 명령은 Windows PowerShell 기준이며, 이 README가 있는 폴더에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

학습용 `training`, `smolvla` extra는 설치하지 않습니다.
LeRobot의 기본 의존성으로 PyTorch 등은 설치될 수 있습니다.
비공개 또는 접근 승인이 필요한 데이터셋은 해당 계정의 Hugging Face 인증이 필요합니다.

## 실행

JSON 목록으로 다운로드 및 병합:

```powershell
.\.venv\Scripts\python.exe datasets_manager.py --links-file configs/dataset_links.json
```

예제 JSON에는 음료 제조 데이터셋 5개의 공개 URL이 들어 있습니다.
원하는 데이터셋으로 목록을 바꿀 수 있습니다.

```json
{
  "urls": [
    "https://huggingface.co/datasets/owner/dataset-a",
    "owner/dataset-b"
  ]
}
```

객체 대신 문자열 배열만 있는 JSON도 지원합니다. `--json`은 `--links-file`과 같은 옵션입니다.

직접 URL 또는 `owner/name` 지정:

```powershell
.\.venv\Scripts\python.exe datasets_manager.py owner/dataset-a owner/dataset-b
```

이후 다른 데이터셋을 추가하면 같은 저장 경로의 catalog에 등록된 기존 원본도 함께 병합합니다.
독립적인 데이터셋 모음을 만들려면 별도 `--storage`를 지정합니다.

```powershell
.\.venv\Scripts\python.exe datasets_manager.py --links-file configs/dataset_links.json --storage datasets --repo-id local/beverage_merged --workers 4
.\.venv\Scripts\python.exe datasets_manager.py --list
.\.venv\Scripts\python.exe datasets_manager.py --verify
.\.venv\Scripts\python.exe datasets_manager.py --help
```

별도 `--storage`를 사용했다면 `--list`와 `--verify`에도 같은 경로를 지정합니다.
Linux/macOS에서는 Python 경로를 `.venv/bin/python`으로 바꿉니다.

## 주요 옵션

| 옵션 | 의미 |
| --- | --- |
| `--links-file`, `--json` | URL 목록 JSON. 여러 번 지정 가능 |
| `--storage` | 저장 폴더. 기본값은 이 프로젝트의 `datasets/` |
| `--revision` | 입력 저장소의 branch/tag/commit. 기본 `main`, 최종 commit은 catalog에 기록 |
| `--workers` | 다운로드 병렬 worker 수. 기본 4 |
| `--repo-id` | 로컬 통합 데이터셋 ID. 기본 `local/beverage_merged` |
| `--allow-overlap` | 같은 원본 시연 또는 raw/masked 변형의 중복 포함 허용 |
| `--list` | 등록된 원본과 현재 병합 결과 출력 |
| `--verify` | 현재 병합 결과의 메타데이터 및 전체 프레임 인덱스 검사 |

`--repo-id`는 로컬 식별자이며 Hugging Face 업로드를 수행하지 않습니다.
`--revision`은 해당 명령에 입력한 모든 원본에 동일하게 적용됩니다.

## 저장 결과

```text
datasets/
  catalog.json
  sources/<owner>/<dataset>/<commit>/
  merged/<fingerprint>-<generation>/
```

- 원본은 commit별 경로에 보존합니다. 재실행 시 기존 파일 크기를 확인해 재사용합니다.
- 데이터셋의 포맷 버전, FPS, 로봇 유형, feature 구성이 같아야 병합할 수 있습니다.
- 공식 `aggregate_datasets`를 사용하며 데이터와 영상을 연결·재인코딩하지 않습니다.
- 에피소드 메타데이터의 파일 참조를 보정할 필요가 있으면 원본 대신 임시 복사본을 수정합니다.
- 병합 결과 검사 후 `catalog.json`을 갱신하고, 이전 병합 세대는 보존합니다.
- 원본 시연의 출처는 병합 결과의 `meta/collection_provenance.json`에 기록합니다.
- 공식 병합 결과는 전체가 train split입니다. 학습·검증 분리는 학습 코드에서 따로 해야 합니다.

다운로드 데이터, 생성된 catalog, 가상환경, 캐시는 `.gitignore`로 제외합니다.
기본 저장 경로 외부에 데이터를 저장할 경우 해당 경로도 Git에서 제외하세요.
이 배포 폴더에는 모델 체크포인트, 학습 로그, 인증 정보가 포함되지 않습니다.
