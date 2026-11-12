with open("/opt/qingsu/tls/default.conf", "r", encoding="utf-8") as f:
    content = f.read()

novel_block = """    # 大秦仙秦科技修仙远程中枢
    location /novel/ {
        proxy_pass http://127.0.0.1:8899/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

"""

if "location /novel/" not in content:
    # 替换两次 location / {，在其上方插入
    parts = content.split("location / {")
    new_content = ""
    for i, part in enumerate(parts):
        if i < len(parts) - 1:
            new_content += part + novel_block + "location / {"
        else:
            new_content += part
    with open("/opt/qingsu/tls/default.conf", "w", encoding="utf-8") as f:
        f.write(new_content)
    print("MODIFIED_SUCCESS")
else:
    print("ALREADY_EXISTS")