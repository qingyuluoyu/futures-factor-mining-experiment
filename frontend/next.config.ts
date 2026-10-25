import type { NextConfig } from "next";

// 通过容器名访问 backend(同 docker network,不经过宿主端口)
const BACKEND_URL =
  process.env.BACKEND_URL ?? "http://qihuo-backend:8001";
const buildDirectory = process.env.WENYUAN_BUILD_DIR ?? ".next";
if (!/^\.next(?:-[a-z0-9-]+)?$/.test(buildDirectory)) throw new Error("Invalid isolated Next build directory");

const nextConfig: NextConfig = {
  output: process.env.WENYUAN_PREVIEW_BUILD === "1" ? undefined : "standalone",
  distDir: buildDirectory,
  // 关闭开发态左下角黑色「N」DevTools 指示球
  devIndicators: false,
  // 同域转发:浏览器请求 /api/* 由 Next.js server 反代到后端
  // 解决 HTTPS 域名(qh.00n.top)访问时调用 HTTP API 的混合内容/CORS 问题
  //
  // 注意：rewrites 用 http-proxy，默认 ~30s 超时且不可配置（见 Next.js #36586）。
  // 因子实验室的 LLM 生成 / GP 搜索常需 1-3 分钟，会触发 30s socket hang up。
  // 故排除 /api/factor-lab/* —— 这些路径改由 Route Handler
  // (app/api/factor-lab/[...path]/route.ts) 接管，该 handler 配置了 5 分钟超时
  // 与完善的错误处理。其余 /api/* 仍走 rewrites。
  async rewrites() {
    return [
      {
        // factor-lab / stock 为长请求路径，由各自 Route Handler 接管（5 分钟超时）
        source: "/api/:path((?!factor-lab|stock).*)",
        destination: `${BACKEND_URL}/api/:path*`,
      },
      {
        source: "/ws/:path*",
        destination: `${BACKEND_URL}/ws/:path*`,
      },
    ];
  },
};

export default nextConfig;
