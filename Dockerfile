# 月影车姬机器人 Dockerfile
# 基于轻量 Python 镜像，适合部署到香港/台湾 VPS

FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装依赖（先复制 requirements.txt 利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 暴露 Web API 端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')" || exit 1

# 启动命令
CMD ["python", "main.py"]
