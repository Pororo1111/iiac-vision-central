import type { NextConfig } from "next";
const config: NextConfig = {
  output: "standalone", poweredByHeader: false,
  devIndicators: false,
  agentRules: false,
  // 로컬 개발 rewrite로 영상(최대 500MB)을 전달할 때 기본 10MB 본문·30초 시간 제한을 늘린다.
  experimental: { proxyTimeout: 600_000, proxyClientMaxBodySize: "510mb" },
  // 사설망 HTTPS 주소(.env의 PUBLIC_ORIGIN)로 dev 서버에 접속할 때만 허용한다.
  allowedDevOrigins: process.env.PUBLIC_ORIGIN ? [new URL(process.env.PUBLIC_ORIGIN).hostname] : [],
  async rewrites() {
    // 로컬 개발에서만 같은 출처 API 요청을 Spring Boot에 전달한다.
    return process.env.DEV_API_ORIGIN ? [{ source: "/api/:path*", destination: `${process.env.DEV_API_ORIGIN}/api/:path*` }] : [];
  },
};
export default config;
