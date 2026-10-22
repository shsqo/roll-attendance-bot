# 롤 디스코드 출석부 봇

## 기능
- 한국 시간 기준 매일 00:05 이후 오늘의 출석부 자동 게시
- `O 참석 가능` / `X 참석 불가` 버튼
- 버튼을 누르지 않으면 미응답
- 오후 7시 마감: O가 5명 이상이면 진행, 미만이면 휴일
- 오후 7시에 별도의 결과 공지 메시지 발송
- SQLite로 출석과 공지 상태를 저장해 재시작 후에도 유지
- 관리자 전용 `!출석부재생성` 복구 명령어

## 1. Discord 봇 만들기
1. https://discord.com/developers/applications 에서 New Application 생성
2. Bot 탭에서 봇을 만들고 토큰을 발급합니다. 토큰은 절대 공개하지 마세요.
3. Bot 설정의 **Privileged Gateway Intents**에서 `Server Members Intent`를 켭니다.
4. OAuth2 > URL Generator에서 `bot` 권한을 선택하고 서버에 초대합니다.
5. 필요한 권한: View Channels, Send Messages, Embed Links, Read Message History, Use External Emojis(선택).
6. 디스코드 사용자 설정 > 고급 > 개발자 모드를 켜고, 출석부를 올릴 채널을 우클릭해 채널 ID를 복사합니다.

## 2. Render에 24시간 배포
이 저장소를 GitHub에 올린 다음 Render에서 **New + > Blueprint**로 저장소를 연결하고 `render.yaml`을 사용합니다.
서비스 환경 변수에 아래 값을 입력합니다.
- `DISCORD_TOKEN`: Bot 탭에서 복사한 토큰
- `DISCORD_CHANNEL_ID`: 출석부를 올릴 텍스트 채널 ID

Render의 디스크가 연결된 Worker로 실행해야 SQLite 데이터가 유지됩니다. 무료 서비스의 수면/제한 정책은 바뀔 수 있으므로 24시간 동작이 필요하면 현재 Render 요금제와 디스크 지원을 확인하세요. 다른 상시 실행 호스팅에서도 같은 환경 변수로 실행할 수 있습니다.

## 3. 로컬 테스트
Python 3.11 이상 권장:
```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```
`.env.example`의 값을 환경 변수로 설정한 뒤:
```bash
python bot.py
```
토큰을 코드에 직접 적거나 GitHub에 커밋하지 마세요.

## 운영 메모
- 출석부는 매일 한국 시간 00:05 이후 생성됩니다. 봇이 그 시간에 꺼져 있으면 재접속 후 생성합니다.
- 오후 7시 이후 봇이 재시작되면 당일 결과 공지를 복구하려고 시도합니다.
- 결과 공지는 중복 방지를 위해 전송 전에 DB에 완료 상태를 기록합니다. 따라서 DB 기록 직후, 디스코드 전송 직전에 프로세스가 강제 종료되면 공지가 누락될 수 있습니다. Discord 전송과 SQLite 기록을 하나의 원자적 작업으로 묶을 수 없기 때문에 엄밀한 exactly-once 보장은 불가능하며, 이 구현은 중복 공지 방지를 우선합니다.
- 투표는 각 Discord 사용자별로 저장되고, O/X 버튼을 다시 누르면 오후 7시 전까지 변경할 수 있습니다.
- 서버 멤버 목록의 이름 표시를 위해 Server Members Intent가 켜져 있어야 합니다. 미응답 수는 별도로 서버 전체 멤버 수를 계산하지 않고 설명으로 표시합니다.
