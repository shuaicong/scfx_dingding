# =============================================================================
# scfx_dingding — 粮达网 → 钉钉知识库 数据采集同步系统
# 基于 Debian 12 bookworm slim，安装 Tesseract OCR（中文）+ Playwright Chromium
# =============================================================================
FROM python:3.12-slim-bookworm

LABEL description="scfx_dingding: 粮达网 → 钉钉知识库 数据采集同步系统"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# ========== 系统依赖 ==========
# 使用腾讯云 Debian 镜像加速国内下载
RUN sed -i 's|deb.debian.org|mirrors.tencent.com|g' /etc/apt/sources.list 2>/dev/null || true; \
    set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        # Tesseract OCR + 中文语言包
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
        # Playwright 核心依赖（不安装 chromium 系统包，由 playwright 管理）
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        # 中文字体
        fonts-noto-cjk \
        # 工具
        curl \
        tzdata; \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# ========== Python 依赖 ==========
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    # Playwright 下载 Chromium 浏览器（约 300MB）到指定路径
    playwright install chromium

# ========== 项目代码 ==========
COPY . .

# 创建数据目录（docker-compose 中通过卷挂载持久化）
RUN mkdir -p /app/data

# ========== 健康检查 ==========
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os; exit(0) if os.path.isdir('/app/data') else exit(1)"

# ========== 启动命令 ==========
ENTRYPOINT ["python", "main.py"]
CMD ["--daemon"]
