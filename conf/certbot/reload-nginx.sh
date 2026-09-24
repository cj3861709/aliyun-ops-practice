#!/bin/bash
# 证书续期成功后重载 nginx
systemctl reload nginx
logger -t certbot-hook "证书已续期，nginx 已重载"
