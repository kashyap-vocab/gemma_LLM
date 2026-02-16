#!/bin/bash

# Quick Start Script for LiveKit Customer Management System
# This script helps you start all components easily

echo "🚀 LiveKit Customer Management System - Quick Start"
echo "=================================================="
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found!"
    echo "Please run: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Check if frontend dependencies are installed
if [ ! -d "frontend/node_modules" ]; then
    echo "❌ Frontend dependencies not installed!"
    echo "Please run: cd frontend && npm install"
    exit 1
fi

echo "Select what to run:"
echo ""
echo "1) Frontend only (React UI)"
echo "2) Backend API only (FastAPI + Smartflo Bridge)"
echo "3) LiveKit Agent only (Voice AI)"
echo "4) All components (opens 3 terminals)"
echo "5) Initialize Database"
echo ""
read -p "Enter choice [1-5]: " choice

case $choice in
    1)
        echo "🎨 Starting Frontend..."
        cd frontend
        npm start
        ;;
    2)
        echo "🔧 Starting Backend API..."
        source venv/bin/activate
        python -m api.main
        ;;
    3)
        echo "🤖 Starting LiveKit Agent..."
        source venv/bin/activate
        python agent/web_rtc_server.py dev
        ;;
    4)
        echo "🚀 Starting all components..."
        echo ""
        echo "Opening 3 terminal windows..."
        
        # For macOS
        if [[ "$OSTYPE" == "darwin"* ]]; then
            osascript -e 'tell app "Terminal" to do script "cd '"$PWD"'/frontend && npm start"'
            osascript -e 'tell app "Terminal" to do script "cd '"$PWD"' && source venv/bin/activate && python -m api.main"'
            osascript -e 'tell app "Terminal" to do script "cd '"$PWD"' && source venv/bin/activate && python agent/web_rtc_server.py dev"'
        # For Linux with gnome-terminal
        elif command -v gnome-terminal &> /dev/null; then
            gnome-terminal -- bash -c "cd frontend && npm start; exec bash"
            gnome-terminal -- bash -c "source venv/bin/activate && python -m api.main; exec bash"
            gnome-terminal -- bash -c "source venv/bin/activate && python agent/web_rtc_server.py dev; exec bash"
        else
            echo "⚠️  Automatic terminal opening not supported on this system"
            echo "Please manually run these commands in 3 separate terminals:"
            echo ""
            echo "Terminal 1: cd frontend && npm start"
            echo "Terminal 2: source venv/bin/activate && python -m api.main"
            echo "Terminal 3: source venv/bin/activate && python agent/web_rtc_server.py dev"
        fi
        ;;
    5)
        echo "🗄️  Initializing Database..."
        source venv/bin/activate
        python db/init_db.py
        echo "✅ Database initialized!"
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac
