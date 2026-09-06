#!/bin/bash
# hk02 MathBoost 一键上线脚本（root 执行）：sudo bash /data/www/hk02/setup-root.sh
set -e
NGINX_CONF=/etc/nginx/sites-available/top580-hstock
TS=$(date +%Y%m%d%H%M%S)

echo "== 1/4 备份 nginx 配置 =="
cp "$NGINX_CONF" "/tmp/top580-hstock.bak.$TS"
echo "已备份: /tmp/top580-hstock.bak.$TS"

echo "== 2/4 安装并启动 systemd 服务 =="
cp /data/www/hk02/hk02.service /etc/systemd/system/hk02.service
systemctl daemon-reload
systemctl enable --now hk02
sleep 2
systemctl is-active hk02 && echo "hk02 服务运行中"
ss -tlnp | grep ':8041' || { echo "错误: 8041 未监听"; exit 1; }

echo "== 3/4 替换 nginx /hk02/ 关停提示页为反向代理 =="
if grep -q '127.0.0.1:8041' "$NGINX_CONF"; then
  echo "nginx 已配置过，跳过"
else
  python3 - <<'PYEOF'
conf = "/etc/nginx/sites-available/top580-hstock"
with open(conf) as f:
    text = f.read()
old = """    # hk02 旧站已关停（/data/www/hk02, 暂未启用），仅作提示页
    location = /hk02 { return 301 /hk02/; }
    location /hk02/ {
        default_type text/html;
        return 200 '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>hk02 已关停</title></head><body style="font-family:sans-serif;text-align:center;padding-top:10vh;"><h1>hk02 已关停</h1><p>该站点已关停，暂未启用。</p><p>河狸投研已迁移至 <a href="/heli/">https://www.top580.com/heli/</a>。</p></body></html>';
    }
"""
new = """    # ===== AI数学成绩提升系统 MathBoost (/data/www/hk02, 127.0.0.1:8041) =====
    location = /hk02 { return 301 /hk02/; }
    location /hk02/ {
        proxy_pass http://127.0.0.1:8041/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /hk02;
        proxy_http_version 1.1;
        proxy_connect_timeout 60s;
        proxy_send_timeout    60s;
        proxy_read_timeout    300s;
    }
"""
if old not in text:
    print("错误: 未找到关停提示块，可能配置已变化，请手动处理")
    raise SystemExit(1)
with open(conf, "w") as f:
    f.write(text.replace(old, new))
print("nginx 配置已更新")
PYEOF
fi

echo "== 4/4 检查并重载 nginx =="
nginx -t && systemctl reload nginx && echo "nginx 已重载"

echo ""
echo "== 验证 =="
echo -n "https://www.top580.com/hk02  -> "; curl -s -o /dev/null -w "%{http_code} (期望301)\n" https://www.top580.com/hk02
echo -n "https://www.top580.com/hk02/ -> "; curl -s -o /dev/null -w "%{http_code} (期望200)\n" https://www.top580.com/hk02/
echo "完成！浏览器访问 https://www.top580.com/hk02/ 首次进入需设置管理密码。"
