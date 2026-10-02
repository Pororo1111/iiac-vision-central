"use client";

import Link from "next/link";

type SideNavProps = {
  active: "dashboard" | "training-data" | "ai-training" | "models" | "video-search";
};

export default function SideNav({ active }: SideNavProps) {
  const inTraining = active === "training-data" || active === "ai-training";

  return (
    <aside className="sidebar">
      <Link className="brand" href="/">
        FIELD<span>CONTROL</span>
      </Link>
      <div className="workspace-label">WORKSPACE</div>
      <nav aria-label="주 메뉴">
        <Link className={active === "dashboard" ? "nav-active" : undefined} href="/">
          <span aria-hidden="true">▦</span> 통합 관제
        </Link>
        <Link className={active === "video-search" ? "nav-active" : undefined} href="/video-search">
          <span aria-hidden="true">⌕</span> 영상 검색
        </Link>
        <Link className={inTraining ? "nav-active" : undefined} href="/ai-training">
          <span aria-hidden="true">✦</span> AI 학습
        </Link>
        <Link className={active === "models" ? "nav-active" : undefined} href="/models">
          <span aria-hidden="true">◈</span> 모델 현황
        </Link>
      </nav>
      <div className="sidebar-bottom">
        <span className="tiny-label">CENTRAL SERVER</span>
        <p>현장을 연결하는 관제</p>
        <span className="version">CONSOLE / 01</span>
      </div>
    </aside>
  );
}
