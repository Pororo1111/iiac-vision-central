"use client";

import { Children, type ReactNode, useEffect, useRef, useState } from "react";

export default function ImageCarousel({ children, label }: { children: ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const count = Children.count(children);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = () => setEdges({ start: node.scrollLeft < 2, end: node.scrollLeft + node.clientWidth >= node.scrollWidth - 2 });
    const observer = new ResizeObserver(update);
    observer.observe(node); node.addEventListener("scroll", update, { passive: true }); update();
    return () => { observer.disconnect(); node.removeEventListener("scroll", update); };
  }, [count]);
  function move(direction: number) {
    const node = ref.current;
    node?.scrollBy({ left: direction * node.clientWidth * 0.9, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }
  return <div className="image-carousel">
    <div className="carousel-controls"><span className="muted">좌우로 넘겨 보기 · {count}장</span><button aria-label={`${label} 왼쪽으로`} disabled={edges.start} onClick={() => move(-1)}>← 이전</button><button aria-label={`${label} 오른쪽으로`} disabled={edges.end} onClick={() => move(1)}>다음 →</button></div>
    <div ref={ref} className="collection-gallery" role="region" aria-label={label} tabIndex={0} onKeyDown={event => {
      if (event.target === event.currentTarget && ["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); move(event.key === "ArrowRight" ? 1 : -1); }
    }}>{children}</div>
  </div>;
}
