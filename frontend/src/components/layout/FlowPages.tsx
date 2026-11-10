import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

// Let the browser paginate actual text and cards at the available page height.
// Columns also split a long paragraph without truncating or duplicating it.
export default function FlowPages({ children, onLoadMore, loading = false }: {
  children: ReactNode;
  onLoadMore?: () => void;
  loading?: boolean;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const autoFillAttempt = useRef("");
  const [page, setPage] = useState(0);
  const [pages, setPages] = useState(1);
  const [pageWidth, setPageWidth] = useState(0);

  useLayoutEffect(() => {
    const outer = viewport.current;
    const inner = content.current;
    if (!outer || !inner) return;
    const measure = () => {
      const width = outer.clientWidth;
      if (!width) return;
      setPageWidth(width);
      const gap = parseFloat(getComputedStyle(inner).columnGap) || 0;
      const count = Math.max(1, Math.ceil((inner.scrollWidth + gap - 1) / (width + gap)));
      setPages(count);
      setPage((current) => Math.min(current, count - 1));
      const last = inner.querySelector("li:last-child");
      if (onLoadMore && !loading && page === count - 1 && last) {
        const rect = last.getBoundingClientRect();
        const attempt = `${inner.querySelectorAll("li").length}:${last.textContent}`;
        // Fetch another batch when at least one more row fits on this page.
        // A failed batch waits for an explicit retry instead of looping requests.
        if (autoFillAttempt.current !== attempt && rect.height > 0 &&
            rect.bottom + rect.height + 12 <= outer.getBoundingClientRect().bottom) {
          autoFillAttempt.current = attempt;
          onLoadMore();
        }
      }
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(outer);
    // Cards and images may acquire a different height after their first render.
    inner.querySelectorAll("img, p, li").forEach((element) => observer?.observe(element));
    window.addEventListener("resize", measure);
    document.fonts?.addEventListener("loadingdone", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
      document.fonts?.removeEventListener("loadingdone", measure);
    };
  }, [children, onLoadMore, loading, page]);

  useLayoutEffect(() => {
    const outer = viewport.current;
    const inner = content.current;
    if (!outer || !inner) return;
    const gap = parseFloat(getComputedStyle(inner).columnGap) || 0;
    outer.scrollLeft = page * (outer.clientWidth + gap);
  }, [page, pages, pageWidth, children]);

  return <div className="flow-pages">
    <div className="flow-pages__viewport" ref={viewport}>
      <div className="flow-pages__content" ref={content}>{children}</div>
    </div>
    <nav className="ledger-pagination" aria-label="页面翻页">
      <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button>
      <span aria-live="polite">{page + 1} / {pages}</span>
      {page + 1 < pages
        ? <button type="button" onClick={() => setPage(page + 1)}>下一页</button>
        : onLoadMore
          ? <button type="button" disabled={loading} onClick={onLoadMore}>{loading ? "加载中…" : "加载更多"}</button>
          : <button type="button" disabled>下一页</button>}
    </nav>
  </div>;
}
