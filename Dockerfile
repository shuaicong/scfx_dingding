# =============================================================================
# scfx_dingding — 粮达网 → 钉钉知识库 数据采集同步系统
# 基于 Debian slim，安装 Tesseract OCR（中文） + Playwright（Chromium）
# =============================================================================
FROM python:3.12-slim

LABEL description="scfx_dingding: 粮达网 → 钉钉知识库 数据采集同步系统"

# 禁止 Python 写 .pyc 文件
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# ========== 系统依赖 ==========
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        # Tesseract OCR + 中文语言包
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
        # Playwright Chromium 依赖
        chromium \
        libxshmfence1 \
        libglib2.0-0 \
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
    # 设置时区
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    # 清理缓存
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# ========== Python 依赖 ==========
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium && \
    playwright install-deps chromium 2>/dev/null || true

# ========== 项目代码 ==========
COPY . .

# 创建数据目录（持久化挂载点）
RUN mkdir -p /app/data

# ========== 健康检查 ==========
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "from config import DB_PATH; import os; exit(0) if os.path.isdir(os.path.dirname(DB_PATH)) else exit(1)"

# ========== 启动命令 ==========
# 默认以守护模式运行，可通过 docker run 覆盖
ENTRYPOINT ["python", "main.py"]
CMD ["--daemon"]
