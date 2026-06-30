FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（可选，用于 lxml 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码（包括 templates 目录）
COPY . .

# 暴露 Web 端口（Flask 默认 5000，但程序使用 15029）
EXPOSE 15029

# 启动命令
CMD ["python", "app.py"]