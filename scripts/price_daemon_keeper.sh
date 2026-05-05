#!/bin/bash
echo "Price daemon keeper started"
while true; do
    if ! pgrep -f "price_daemon.py" > /dev/null; then
        echo "Price daemon down - restarting"
        cd /root/gold-signals/pipeline
        python3 price_daemon.py &
        curl -s "https://api.telegram.org/bot8680392041:AAFKpVrYzWHQrR-4_-BbmA-eWIC6BV4Zp8s/sendMessage?chat_id=1273237796&text=⚠️+Price+daemon+restarted"
    fi
    sleep 15
done
