# =============================================================================
# scfx_dingding — 粮达网 → 钉钉知识库 数据采集同步系统
# 基于 Debian 12 bookworm slim，安装 Tesseract OCR（中文）+ Playwright Chromium
# =============================================================================
FROM python:3.12-slim-bookworm

LABEL description="scfx_dingding: 粮达网 → 钉钉知识库 数据采集同步系统"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PIP_INDEX_URL=http://mirrors.tencent.com/pypi/simple \
    PIP_TRUSTED_HOST=mirrors.tencent.com

# ========== 系统依赖 ==========
# 使用腾讯云 Debian 镜像加速国内下载
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; \
    printf 'Types: deb\nURIs: http://mirrors.tencent.com/debian\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: http://mirrors.tencent.com/debian-security\nSuites: bookworm-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' > /etc/apt/sources.list.d/debian.sources; \
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
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# 从预下载的压缩包安装 Chromium（避免国内无法访问 CDN）
# 参考: https://storage.googleapis.com/chrome-for-testing-public/
COPY chrome-linux64.zip /tmp/
RUN mkdir -p /opt/playwright && \
    unzip -q /tmp/chrome-linux64.zip -d /opt/playwright/chromium-1228/ && \
    rm -f /tmp/chrome-linux64.zip && \
    chmod +x /opt/playwright/chromium-1228/chrome-linux64/chrome && \
    # 创建 Playwright 注册文件，标记浏览器已安装
    mkdir -p /opt/playwright && \
    echo "1228" > /opt/playwright/chromium-1228/INSTALLATION_COMPLETE

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
