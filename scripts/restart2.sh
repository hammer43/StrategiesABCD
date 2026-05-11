#!/bin/bash
echo "Starting force multiplier strategies..."
screen -dmS goldv3 python3 /root/gold-signals/system/main_v3_rr035_ml40_fm.py
sleep 5
echo "Running screens:"
screen -list
