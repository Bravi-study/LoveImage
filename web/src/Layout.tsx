import { useEffect, useLayoutEffect, useState, type Dispatch, type SetStateAction } from "react";
import { Link, Outlet, useLocation, useOutletContext } from "react-router";

import * as api from "./api";
import type { Problems } from "./errors";
import { LINK, SHELL, Wordmark } from "./ui";

/** What the visitor put on the form. Lives here so a rejected shoot can send them back with everything in place. */
export type Draft = { photo1: File | null; photo2: File | null; wish: string; problems: Problems };

/** nickname is undefined until /api/me answers, null for a guest, a string for an account. */
export type Session = {
  nickname: string | null | undefined;
  setNickname: (nickname: string | null) => void;
  draft: Draft;
  setDraft: Dispatch<SetStateAction<Draft>>;
};
export const useSession = () => useOutletContext<Session>();

export function Layout() {
  const { pathname } = useLocation();
  const [nickname, setNickname] = useState<string | null | undefined>(undefined);
  const [draft, setDraft] = useState<Draft>({ photo1: null, photo2: null, wish: "", problems: {} });

  useEffect(() => {
    let live = true;
    api
      .me()
      .then((m) => {
        if (live) setNickname(m.nickname);
      })
      .catch(() => {
        // nickname stays undefined (unknown): the guest note stays hidden and the page works without the answer.
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    // Without this a file dropped next to a card makes the browser leave the site to open it. Text dropped into a field must still land.
    const keep = (e: DragEvent) => {
      const field = e.target instanceof Element && e.target.closest("input,textarea");
      if (e.dataTransfer?.types.includes("Files") || !field) e.preventDefault();
    };
    window.addEventListener("dragover", keep);
    window.addEventListener("drop", keep);
    return () => {
      window.removeEventListener("dragover", keep);
      window.removeEventListener("drop", keep);
    };
  }, []);

  // A layout effect: it runs before the page's own effects, which may scroll to a problem on the form.
  useLayoutEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [pathname]);

  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:bg-paper focus:px-4 focus:py-2">
        К содержанию
      </a>
      <header className="flex h-16 shrink-0 items-center justify-between px-5 text-wine lg:h-[4.5rem] lg:px-12">
        <Link to="/" aria-label="LoveImage, на главную">
          <Wordmark />
        </Link>
        <nav aria-label="Разделы">
          <Link to="/my" aria-current={pathname === "/my" ? "page" : undefined} className={`inline-flex min-h-11 items-center text-[15px] font-medium ${LINK}`}>
            Мои снимки
          </Link>
        </nav>
      </header>

      <main id="main" tabIndex={-1} className="flex flex-1 flex-col outline-none">
        <Outlet context={{ nickname, setNickname, draft, setDraft } satisfies Session} />
      </main>

      <footer className="on-dark border-t border-ivory/10 bg-wine-deep text-blush">
        <div className={`${SHELL} flex flex-col gap-3 py-8 text-sm sm:flex-row sm:items-center sm:justify-between`}>
          <span className="text-ivory">
            <Wordmark />
          </span>
          <p>18+. Только взрослые, без откровенных снимков.</p>
          <Link to="/privacy" className={LINK}>
            Как мы обращаемся с фото
          </Link>
        </div>
      </footer>
    </div>
  );
}
