# 矩形纸张裁切排样应用
# 仅使用 Python 标准库（wsgiref / sqlite3 / http.server），无第三方依赖。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DB_PATH=/data/cutting.db

WORKDIR /app

# 仅拷贝应用代码（无 requirements.txt 安装步骤——零第三方依赖）
COPY app/ /app/app/

# 数据卷：SQLite 数据库持久化
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# 容器启动即初始化数据库（首次启动写入默认输入），随后启动服务
CMD ["python3", "-u", "/app/app/server.py"]
