# 后端镜像 —— 多阶段构建,基于 uv
# Stage 1: 安装依赖到虚拟环境
# 基础镜像走 nju ghcr 中转（构建机在国内，ghcr.io 直连拉取 oauth 被拒）；
# 如需回官方源：docker build --build-arg UV_BASE=ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ARG UV_BASE=ghcr.nju.edu.cn/astral-sh/uv:python3.12-bookworm-slim
FROM ${UV_BASE} AS builder
WORKDIR /app

# 先复制 lock 文件,利用 Docker 缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 复制源码并安装项目
COPY . .
RUN uv sync --frozen --no-dev

# Stage 2: 精简运行时镜像
FROM python:3.12-slim AS runtime
WORKDIR /app

ARG GIT_REVISION
ARG IMAGE_VERSION
ARG IMAGE_SOURCE=https://github.com/roberts9012062/futures-factor-mining-experiment
LABEL org.opencontainers.image.revision="$GIT_REVISION" \
      org.opencontainers.image.version="$IMAGE_VERSION" \
      org.opencontainers.image.source="$IMAGE_SOURCE"

# 复制虚拟环境和应用代码
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    APP_ENV=production \
    LOG_LEVEL=INFO

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8001/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
