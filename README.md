# Earth Previz

**좌표 또는 주소 하나로 에이리얼 시네마틱 프리비즈 영상을 자동 생성합니다.**

Google Photorealistic 3D Tiles + CesiumJS 기반. 드론/헬리콥터 촬영 사전시각화 전문 도구.

## 주요 기능

- 주소 또는 좌표 입력 → 5~10개의 다양한 에이리얼 샷 자동 생성
- 오비트, 플라이바이, 플라이스루, 디센트/어센트 등 10가지 카메라 프리셋
- 카메라 고도, 반경, 방위각, 틸트, 속도를 슬라이더로 실시간 조절
- 270p 프리뷰 → 4K 최종 렌더링
- MP4 상단에 고도/속도/추천 플랫폼(드론 vs 헬기) 메타데이터 자동 베이크
- KML (Google Earth Pro 투어) + JSX (After Effects 카메라 데이터) 자동 출력

## 요구사항

- **Python 3.9+**
- **FFmpeg** (시스템에 설치되어 있어야 함)
- **Google Maps Platform API Key** (Map Tiles API 활성화 + 빌링 연결 필요)

## 설치

```bash
# 1. 저장소 클론
git clone https://github.com/mungmung0601/earth-previz.git
cd earth-previz

# 2. 의존성 설치
pip install -r requirements.txt

# 3. Playwright 브라우저 설치
python -m playwright install chromium

# 4. (선택) .env 파일 생성
cp .env.example .env
# .env 파일을 열어 GOOGLE_MAPS_API_KEY 입력
```

### FFmpeg 설치

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows (Chocolatey)
choco install ffmpeg
```

## 실행

### 웹 앱 (권장)

```bash
python app.py
```

브라우저에서 `http://127.0.0.1:5100` 접속.

### 또는 1-click 실행

```bash
# macOS / Linux
./run.sh

# Windows
run.bat
```

### CLI 모드

```bash
# 주소로 생성
python bot.py --place "1472 Broadway, New York" --shots 5 --duration-sec 7 --resolution 480p

# 좌표로 생성
python bot.py --lat 40.756 --lng -73.986 --shots 3 --duration-sec 10 --resolution 720p

# dry-run (메타데이터 + KML/JSX만 생성, 렌더링 생략)
python bot.py --place "서울시청" --shots 10 --dry-run
```

## 사용 흐름 (웹 앱)

1. **API 키 입력** → 자동으로 빌링 연결 상태 확인
2. **주소 또는 좌표 입력** + 샷 수, 영상 길이, 해상도, 텍스처 선택
3. **프리뷰 생성** → 진행률 바로 실시간 표시
4. **영상 선택** → 카메라 파라미터 슬라이더로 편집
5. **Generate** → 원하는 해상도 (최대 4K)로 최종 렌더링 + 다운로드

## 출력 파일

```
output/run_YYYYMMDD_HHMMSS/
├── videos/          # MP4 파일 (메타데이터 오버레이 포함)
├── kml/             # Google Earth Pro 투어 파일
├── jsx/             # After Effects 카메라 스크립트
└── metadata/        # JSON/CSV 분석 데이터
```

## 프로젝트 구조

```
earth-previz/
├── app.py              # Flask 웹 앱 (메인 진입점)
├── bot.py              # CLI 모드
├── camera_path.py      # 10가지 카메라 경로 프리셋
├── renderer.py         # Playwright + CesiumJS 헤드리스 렌더링
├── encoder.py          # FFmpeg MP4 인코딩
├── recommender.py      # 드론 vs 헬기 추천 분석
├── geocoder.py         # 주소 → 좌표 변환
├── models.py           # 데이터 모델 (CameraKeyframe, ShotPlan)
├── kml_exporter.py     # KML 투어 출력
├── jsx_exporter.py     # After Effects JSX 출력
├── esp_parser.py       # Earth Studio ESP 파일 파서
├── esp_exporter.py     # ESP 호환 출력
├── web/
│   └── viewer.html     # CesiumJS 3D 뷰어
├── templates/
│   └── index.html      # 웹 앱 UI
├── requirements.txt
├── run.sh              # macOS/Linux 실행 스크립트
├── run.bat             # Windows 실행 스크립트
└── .env.example
```

## Google Maps API 키 발급

1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. 프로젝트 생성 또는 선택
3. [결제(Billing)](https://console.cloud.google.com/billing) 계정 연결
4. [Map Tiles API](https://console.cloud.google.com/apis/library/tile.googleapis.com) 사용 설정
5. [사용자 인증 정보](https://console.cloud.google.com/apis/credentials) 에서 API 키 생성
6. 앱에서 키 입력 또는 `.env` 파일에 저장

## License

MIT
