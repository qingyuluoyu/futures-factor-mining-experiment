# CTP 行情桥镜像 —— 虚拟盘(openctp_tts) 与实盘前置(openctp_ctp) 双模式
# 与 backend 相同的多阶段构建，另装 CTP 库，入口为 md_bridge。
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

# CTP 库不在 lock 内（按渠道可选），在此补装；失败不阻断（无 CTP 渠道的服务器仍可部署其余组件）
RUN uv pip install --python /app/.venv/bin/python openctp-tts openctp-ctp \
    || echo "openctp 安装失败，CTP 行情桥不可用"

FROM python:3.12-slim AS runtime
WORKDIR /app

ARG GIT_REVISION
ARG IMAGE_VERSION
ARG IMAGE_SOURCE=https://github.com/roberts9012062/futures-factor-mining-experiment
LABEL org.opencontainers.image.revision="$GIT_REVISION" \
      org.opencontainers.image.version="$IMAGE_VERSION" \
      org.opencontainers.image.source="$IMAGE_SOURCE"

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

CMD ["python", "-m", "app.connectors.ctp.md_bridge"]
