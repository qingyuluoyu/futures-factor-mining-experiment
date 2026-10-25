# 前端镜像 —— 多阶段构建,Next.js standalone 输出
# Stage 1: 安装依赖(使用 pnpm 9,兼容 lockfileVersion 9.0,无 build approval 限制)
# 使用淘宝镜像源避免内网环境拉 npm 超时(ETIMEDOUT)
# 注意: corepack prepare 默认打 registry.npmjs.org,内网常超时,改用 npm 全局安装
FROM node:22-alpine AS deps
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g pnpm@9.15.4
WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN pnpm config set registry https://registry.npmmirror.com \
    && pnpm install --frozen-lockfile

# Stage 2: 构建 standalone 产物
FROM node:22-alpine AS builder
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g pnpm@9.15.4
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Next.js NEXT_PUBLIC_* 变量在 build 时固化,必须在此注入
# API_BASE 留空 → 前端用相对路径 /api/* → Next.js rewrites 转发到 BACKEND_URL
# 这样无论从内网 IP 还是 HTTPS 域名访问都同源,无 CORS/Mixed Content 问题
ARG NEXT_PUBLIC_API_URL=
# WS_URL 留空 → resolveWsBase 按访问 hostname 自动判定：
# 配置 NEXT_PUBLIC_WS_PUBLIC_HOST 时该域名走反代端口(默认3051)，其他→8002(本地直连)
# 公网域名经反代部署时注入 PUBLIC_HOST，内网/本地一次构建两边都对
ARG NEXT_PUBLIC_WS_URL=
ARG NEXT_PUBLIC_WS_PUBLIC_HOST=
ARG NEXT_PUBLIC_WS_PUBLIC_PORT=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL
ENV NEXT_PUBLIC_WS_PUBLIC_HOST=$NEXT_PUBLIC_WS_PUBLIC_HOST
ENV NEXT_PUBLIC_WS_PUBLIC_PORT=$NEXT_PUBLIC_WS_PUBLIC_PORT
ENV BACKEND_URL=http://qihuo-backend:8001
ENV NEXT_TELEMETRY_DISABLED=1

RUN pnpm build

# Stage 3: 精简运行时镜像
FROM node:22-alpine AS runner
WORKDIR /app

ARG GIT_REVISION
ARG IMAGE_VERSION
ARG IMAGE_SOURCE=https://github.com/roberts9012062/futures-factor-mining-experiment
LABEL org.opencontainers.image.revision="$GIT_REVISION" \
      org.opencontainers.image.version="$IMAGE_VERSION" \
      org.opencontainers.image.source="$IMAGE_SOURCE"

# BACKEND_URL 供 server 侧 Route Handler / rewrites 访问同网后端
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    BACKEND_URL=http://qihuo-backend:8001

# 非 root 用户运行
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# 仅复制 standalone 运行所需文件
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
# 静态资源（首页 showcase 截图等）standalone 默认不带 public
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
