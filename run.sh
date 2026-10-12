#!/bin/bash
# AI 고객응대 챗봇 (단골 자동화 패키지 03) 실행 스크립트
cd "$(dirname "$0")"

PORT=5006
while (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; do
  PORT=$((PORT + 1))
  if [ "$PORT" -gt 5020 ]; then
    echo "사용 가능한 포트가 없습니다. (5006~5020 전부 사용 중)"
    exit 1
  fi
done

if [ "$PORT" != "5006" ]; then
  echo "[알림] 5006번 포트가 사용 중이라 ${PORT}번 포트로 실행합니다."
fi

python3 -c "import flask" 2>/dev/null || pip install -r requirements.txt

echo "==================================================="
echo " 🤖 AI 고객응대 챗봇 실행 중 (포트: $PORT)"
echo " 👤 손님용 채팅 : http://localhost:$PORT/chat"
echo " 👔 사장님 관리 : http://localhost:$PORT/admin"
echo "    (종료: Ctrl+C)"
echo "==================================================="
python3 app.py --port "$PORT"
