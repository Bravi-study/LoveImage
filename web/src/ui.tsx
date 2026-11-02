import { useEffect, useRef, useState, type ReactNode } from "react";

export const SHELL = "w-full px-5 lg:px-12";
export const BUTTON =
  "inline-flex min-h-14 items-center justify-center gap-2.5 rounded-[2px] bg-wine px-7 text-center font-display text-[1.45rem] font-semibold leading-tight text-ivory transition-colors hover:bg-rosewood disabled:cursor-wait disabled:opacity-70";
export const BUTTON_QUIET =
  "inline-flex min-h-14 items-center justify-center gap-2.5 rounded-[2px] border border-wine/40 px-7 text-center font-display text-[1.45rem] font-semibold leading-tight text-wine transition-colors hover:bg-blush-soft disabled:cursor-wait disabled:opacity-70";
export const LINK = "underline decoration-1 underline-offset-4 transition-colors hover:text-rosewood hover:decoration-2";
export const ALERT = "text-sm font-medium leading-relaxed text-rosewood";

/** The one heading of a screen. Takes focus so a screen reader announces the new page. */
export function Title({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
  }, []);
  return (
    <h1 ref={ref} tabIndex={-1} className={`font-display text-balance outline-none ${className}`}>
      {children}
    </h1>
  );
}

export function Wordmark() {
  return (
    <span className="font-display text-[1.75rem] font-medium leading-none tracking-[-0.01em]">
      Love
      <span aria-hidden className="mx-[0.14em] inline-block h-[0.82em] w-px translate-y-[0.1em] bg-current opacity-60" />
      <em>Image</em>
    </span>
  );
}

/** The "and" between two people: the sign of the site, set on the seam of a pair of photos. */
export function Seal({ className = "" }: { className?: string }) {
  return (
    <span aria-hidden className={`pointer-events-none grid place-items-center rounded-full bg-wine font-display italic text-ivory ring-1 ring-ivory/50 ${className}`}>
      <span className="-translate-y-[0.08em] leading-none">и</span>
    </span>
  );
}

function Half({ src, alt, className = "" }: { src: string; alt: string; className?: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return <span className="block size-full" />;
  return <img src={src} alt={alt} loading="lazy" onError={() => setBroken(true)} className={`size-full object-cover object-[50%_25%] ${className}`} />;
}

/** Two photos on one seam. A photo that fails to load leaves the blush ground, which is also the placeholder. */
export function PhotoPair({ urls, alts = ["", ""], seal = false, className = "", imgClassName = "", drift = false }: { urls: string[]; alts?: string[]; seal?: boolean; className?: string; imgClassName?: string; drift?: boolean }) {
  return (
    <div className={`relative grid grid-cols-2 bg-blush-soft ${drift ? "" : "overflow-hidden"} ${className}`}>
      {urls.map((url, i) => (
        <div key={url} className={`min-h-0 overflow-hidden ${drift ? (i === 0 ? "anim-drift-left" : "anim-drift-right") : ""}`}>
          <Half src={url} alt={alts[i] ?? ""} className={imgClassName} />
        </div>
      ))}
      {!drift && <span aria-hidden className="absolute inset-y-0 left-1/2 w-px bg-ivory/70" />}
      {seal && <Seal className="absolute top-[62%] left-1/2 size-10 -translate-x-1/2 -translate-y-1/2 text-2xl" />}
    </div>
  );
}

/** Deleting is final, so it takes a second, separate click. Focus lands on the safe answer. */
export function DeleteButton({ label, onDelete, className = "" }: { label: string; onDelete: () => void; className?: string }) {
  const [asking, setAsking] = useState(false);
  const wasAsking = useRef(false);
  const first = useRef<HTMLButtonElement>(null);
  const keep = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (asking) keep.current?.focus();
    else if (wasAsking.current) first.current?.focus();
    wasAsking.current = asking;
  }, [asking]);

  const TEXT = `min-h-11 text-sm ${LINK}`;
  if (!asking) {
    return (
      <button ref={first} type="button" onClick={() => setAsking(true)} aria-label={label} className={`${TEXT} text-ink-soft ${className}`}>
        Удалить
      </button>
    );
  }
  return (
    <span role="group" aria-label={label} className={`flex flex-wrap items-center gap-x-3 text-sm ${className}`}>
      <span className="whitespace-nowrap">Удалить насовсем?</span>
      <span className="flex gap-x-3">
        <button type="button" onClick={onDelete} className={`${TEXT} font-semibold text-rosewood`}>
          Да
        </button>
        <button ref={keep} type="button" onClick={() => setAsking(false)} className={TEXT}>
          Нет
        </button>
      </span>
    </span>
  );
}

function Icon({ children, className = "size-5" }: { children: ReactNode; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {children}
    </svg>
  );
}

export const PlusIcon = () => (
  <Icon className="size-4">
    <path d="M12 5v14M5 12h14" />
  </Icon>
);

export const CloseIcon = () => (
  <Icon className="size-4">
    <path d="m6 6 12 12M18 6 6 18" />
  </Icon>
);

export const DownloadIcon = () => (
  <Icon>
    <path d="M12 4v11M7.5 11l4.5 4.5 4.5-4.5M5 20h14" />
  </Icon>
);

export const CheckIcon = () => (
  <Icon className="size-4">
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Icon>
);
