#!/bin/bash
# ============================================================
# Single script to build, start, and serve everything globally
# ============================================================
# Usage:
#   ./start.sh           - Start everything (build + agent + API)
#   ./start.sh --ngrok   - Also start ngrok tunnel for global URL
# ============================================================

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  LTFS Survey Agent - Full Stack Launcher   ${NC}"
echo -e "${CYAN}============================================${NC}"

# Check .env
if [ ! -f .env ]; then
    echo -e "${RED}ERROR: .env file not found. Copy .env.sample to .env and fill in values.${NC}"
    exit 1
fi

# Cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down all services...${NC}"
    kill $AGENT_PID $API_PID $NGROK_PID 2>/dev/null
    wait $AGENT_PID $API_PID $NGROK_PID 2>/dev/null
    echo -e "${GREEN}All services stopped.${NC}"
}
trap cleanup EXIT

# ---- Step 1: Build Frontend ----
echo -e "\n${GREEN}[1/4] Building React frontend...${NC}"
cd "$PROJECT_ROOT/frontend"
if [ ! -d node_modules ]; then
    echo "Installing frontend dependencies..."
    npm install --silent
fi
rm -rf build
npm run build 2>&1 | tail -3
echo -e "${GREEN}Frontend built successfully.${NC}"

# ---- Step 2: Activate venv ----
echo -e "\n${GREEN}[2/4] Activating Python virtual environment...${NC}"
cd "$PROJECT_ROOT"
if [ -d "venv/Scripts" ]; then
    # Windows Git Bash
    source venv/Scripts/activate
elif [ -d "venv/bin" ]; then
    # Linux/macOS
    source venv/bin/activate
else
    echo -e "${YELLOW}No venv found, using system Python.${NC}"
fi
echo "Python: $(which python)"

# ---- Step 3: Start LiveKit Agent ----
echo -e "\n${GREEN}[3/4] Starting LiveKit Agent...${NC}"
cd "$PROJECT_ROOT"
python agent/web_rtc_server.py dev &
AGENT_PID=$!
echo -e "Agent PID: ${AGENT_PID}"

# Wait for agent to register
sleep 3

# ---- Step 4: Start API Server (serves frontend + API + WebSocket bridge) ----
echo -e "\n${GREEN}[4/4] Starting API Server on port 8000 (5 workers)...${NC}"
cd "$PROJECT_ROOT"
python api/main.py &
API_PID=$!
echo -e "API PID: ${API_PID}"

sleep 2

echo -e "\n${CYAN}============================================${NC}"
echo -e "${GREEN}  All services running!${NC}"
echo -e "${CYAN}============================================${NC}"
echo -e "  Local URL:  ${GREEN}http://localhost:8000${NC}"

# ---- Optional: Start ngrok ----
if [ "$1" = "--ngrok" ]; then
    if command -v ngrok &>/dev/null; then
        echo -e "\n${GREEN}Starting ngrok tunnel...${NC}"
        ngrok http 8000 --log=stdout > /tmp/ngrok.log 2>&1 &
        NGROK_PID=$!

        # Wait for ngrok to start and extract URL
        sleep 3
        NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python -c "import sys,json; t=json.load(sys.stdin)['tunnels']; print(next((x['public_url'] for x in t if 'https' in x['public_url']), t[0]['public_url'] if t else 'N/A'))" 2>/dev/null || echo "N/A")

        if [ "$NGROK_URL" != "N/A" ]; then
            echo -e "  Global URL: ${GREEN}${NGROK_URL}${NC}"
            echo -e "  SmartFlo WS: ${GREEN}${NGROK_URL/https/wss}/smartflo/stream${NC}"
            echo ""
            echo -e "  ${YELLOW}Update your SmartFlo dashboard WebSocket URL to:${NC}"
            echo -e "  ${CYAN}${NGROK_URL/https/wss}/smartflo/stream${NC}"
        else
            echo -e "  ${YELLOW}ngrok started but could not extract URL. Check http://localhost:4040${NC}"
        fi
    else
        echo -e "\n${YELLOW}ngrok not found. Install it:${NC}"
        echo -e "  ${CYAN}choco install ngrok${NC}  (Windows)"
        echo -e "  ${CYAN}brew install ngrok${NC}   (macOS)"
        echo -e "  ${CYAN}snap install ngrok${NC}   (Linux)"
        echo ""
        echo -e "  Or use another tunnel like ${CYAN}cloudflared tunnel${NC}"
    fi
fi

echo -e "\n${YELLOW}Press Ctrl+C to stop all services.${NC}"

# Wait for background processes
wait
