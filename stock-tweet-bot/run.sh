#!/usr/bin/env bash
set -e
cd /www/wwwroot/stock-tweet-bot
export TZ=Asia/Shanghai
python3 fetch.py >> run.log 2>&1
date '+[%Y-%m-%d %H:%M:%S] fetched' >> run.log
echo '---' >> run.log
