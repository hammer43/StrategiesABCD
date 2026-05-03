#!/bin/bash
while true; do
    # Check Strategy A
    if ! pgrep -f "main.py" > /dev/null; then
        echo "Strategy A down - restarting"
        cd /root/gold-signals/system
        screen -dmS goldv2 python3 main.py
        curl -s "https://api.telegram.org/bot8680392041:AAFKpVrYzWHQrR-4_-BbmA-eWIC6BV4Zp8s/sendMessage?chat_id=1273237796&text=⚠️+Strategy+A+restarted"
    fi
    
    # Check pipeline strategies
    for strat in strategy_b strategy_c strategy_d; do
        if ! pgrep -f "$strat" > /dev/null; then
            echo "$strat down - restarting pipeline"
            /root/gold-signals/scripts/restart.sh
            curl -s "https://api.telegram.org/bot8680392041:AAFKpVrYzWHQrR-4_-BbmA-eWIC6BV4Zp8s/sendMessage?chat_id=1273237796&text=⚠️+Pipeline+restarted"
            break
        fi
    done
    
    sleep 300  # Check every 5 minutes
done
