#!/bin/bash
# 一键部署脚本（需要 sudo 的部分由用户执行，本脚本仅打印命令指引）
set -e
cd "$(dirname "$0")"

echo "== hk02 MathBoost 部署指引 =="
echo ""
echo "1) 安装并启动 systemd 服务（root 执行）："
echo "   sudo cp /data/www/hk02/hk02.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now hk02"
echo ""
echo "2) 替换 nginx 中 /hk02/ 关停提示页为反向代理（root 执行）："
echo "   先备份：sudo cp /etc/nginx/sites-available/top580-hstock /tmp/top580-hstock.bak.\$(date +%Y%m%d%H%M%S)"
echo "   定位：  sudo grep -n 'hk02 旧站已关停' /etc/nginx/sites-available/top580-hstock"
echo "   将该注释行起至 location /hk02/ { ... } 块结束（约 5 行）替换为 nginx-hk02.conf 内容"
echo "   检查：  sudo nginx -t && sudo systemctl reload nginx"
echo ""
echo "3) 验证："
echo "   systemctl status hk02"
echo "   curl -s -o /dev/null -w '%{http_code}\\n' https://www.top580.com/hk02/   # 期望 200"
