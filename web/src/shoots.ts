import type { Job, Shoot, ShootCard } from "./api.ts";

const SHOOTS_PAGE = 30;
const PICTURES = new Intl.PluralRules("ru");
const PICTURE_WORD = { one: "снимок", few: "снимка", many: "снимков", other: "снимка" } as Record<string, string>;

export const isActive = (job: Job) => job.status !== "succeeded" && job.status !== "failed";

/** Whether a visitor is still waiting for this shoot to change, which is also how long the page polls. */
export const isBusy = (shoot: Shoot) => shoot.status === "uploaded" || shoot.jobs.some(isActive);

export type View =
  | { name: "checking" }
  | { name: "check_failed"; code: string | null }
  | { name: "rejected"; reason: string | null }
  | { name: "no_jobs" }
  | { name: "first_picture" }
  | { name: "pictures"; job: Job };

export function pickView(shoot: Shoot, selected: number | null): View {
  if (shoot.status === "uploaded") return { name: "checking" };
  if (shoot.status === "failed") return { name: "check_failed", code: shoot.reject_reason };
  if (shoot.status === "rejected") return { name: "rejected", reason: shoot.reject_reason };
  const latest = shoot.jobs.at(-1);
  if (!latest) return { name: "no_jobs" };
  if (shoot.jobs.length === 1 && isActive(latest)) return { name: "first_picture" };
  return { name: "pictures", job: shoot.jobs.find((j) => j.variant === selected) ?? latest };
}

export function statusText(view: View) {
  if (view.name === "checking") return "Проверяем снимки.";
  if (view.name === "first_picture") return "Снимки подошли. Создаём ваш снимок.";
  if (view.name === "check_failed") return "Не получилось проверить снимки.";
  if (view.name === "rejected") return "Снимки не прошли проверку.";
  if (view.name === "no_jobs") return "Снимки проверены, снимок ещё не заказан.";
  if (isActive(view.job)) return `Создаём вариант ${view.job.variant}.`;
  return view.job.status === "succeeded" ? `Вариант ${view.job.variant} готов.` : `Вариант ${view.job.variant} не получился.`;
}

export const cardWorking = (card: ShootCard) => card.status === "uploaded" || card.active;

/** A short page means the list has ended. A full one may still be the last: the next request then comes back empty. */
export const hasMore = (page: ShootCard[]) => page.length === SHOOTS_PAGE;

/** A refresh reads only the newest page, so cards from "show more" stay as they are. */
export function mergeCards(all: ShootCard[], fresh: ShootCard[]) {
  const byId = new Map(fresh.map((card) => [card.id, card]));
  const known = new Set(all.map((card) => card.id));
  return [...fresh.filter((card) => !known.has(card.id)), ...all.map((card) => byId.get(card.id) ?? card)];
}

export const picturesLabel = (n: number) => `${n} ${PICTURE_WORD[PICTURES.select(n)]}`;

export function dateLabel(iso: string, now = new Date()) {
  const date = new Date(iso);
  return date.toLocaleString("ru-RU", {
    day: "numeric",
    month: "long",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
