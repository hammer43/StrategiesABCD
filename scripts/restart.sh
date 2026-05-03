#!/bin/bash
echo "Stopping all screens..."
screen -X -S pipeline quit 2>/dev/null
screen -X -S goldv2 quit 2>/dev/null

echo "Checking session..."
if [ ! -f /root/gold-signals/gold_session.session ]; then
  cp /root/gold_session_backup.session /root/gold-signals/gold_session.session
fi
echo "Clearing queue..."
echo "[]" > /root/gold-signals/pipeline/signal_queue.json

echo "Starting pipeline..."
cd /root/gold-signals/pipeline
screen -dmS pipeline python3 run.py

echo "Starting goldv2..."
cd /root/gold-signals/system
screen -dmS goldv2 python3 main.py

sleep 5
echo "Running screens:"
screen -list
