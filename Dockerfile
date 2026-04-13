# 月影车姬机器人 Dockerfile（Railway 版）
# 基于轻量 Python 镜像，适合部署到 Railway 云平台

FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装依赖（先复制 requirements.txt 利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 暴露端口（Railway 会注入 $PORT，默认 8080）
EXPOSE 8080

# 健康检查（检测 Web API 是否响应）
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8080}/')" || exit 1

# 启动命令
CMD ["python", "main.py"]
