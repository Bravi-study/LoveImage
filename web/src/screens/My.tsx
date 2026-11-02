import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import * as api from "../api";
import { HttpError, type ShootCard } from "../api";
import { deleteErrorText, PHOTO_LABEL } from "../errors";
import { useSession } from "../Layout";
import { cardWorking, dateLabel, hasMore, mergeCards, picturesLabel } from "../shoots";
import { ALERT, BUTTON, BUTTON_QUIET, DeleteButton, LINK, PhotoPair, SHELL, Title } from "../ui";

const REFRESH_MS = 8000;
const OFFLINE = "Нет связи с сервером. Попробуйте ещё раз.";

function cardNote(card: ShootCard) {
  if (card.status === "uploaded") return "Проверяем снимки…";
  if (card.active) return "Создаём снимок…";
  if (card.status === "failed") return "Проверка не удалась, можно повторить";
  return "Снимка пока нет";
}

function Card({ card, onDelete }: { card: ShootCard; onDelete: () => void }) {
  const working = cardWorking(card);
  const title = card.wish || "Без пожелания";
  return (
    <li className="min-w-0">
      <Link to={`/shoot/${card.id}`} className="group block">
        <div className="relative overflow-hidden bg-blush-soft">
          {card.cover_url ? (
            <img src={card.cover_url} alt={`Снимок: ${title}`} loading="lazy" className="aspect-[3/4] w-full object-cover transition-transform duration-700 group-hover:scale-[1.02]" />
          ) : (
            <PhotoPair urls={[1, 2].map((n) => `/api/shoots/${card.id}/photos/${n}`)} alts={[`Исходное фото: ${PHOTO_LABEL.photo1}`, `Исходное фото: ${PHOTO_LABEL.photo2}`]} seal className="aspect-[3/4]" imgClassName={working ? "anim-develop" : ""} />
          )}
          {(working || card.images === 0) && (
            <span className="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-wine-deep/85 px-3 py-2 text-[13px] leading-snug text-ivory">
              {working && <span aria-hidden className="anim-pulse-dot size-1.5 shrink-0 rounded-full bg-blush" />}
              {cardNote(card)}
            </span>
          )}
        </div>
        <p className="mt-2.5 line-clamp-2 font-display text-[1.35rem] leading-[1.15] text-wine italic group-hover:underline group-hover:decoration-1 group-hover:underline-offset-4">{title}</p>
      </Link>
      <p className="mt-1 text-[13px] text-ink-soft">{dateLabel(card.created_at)}</p>
      <div className="flex flex-wrap items-center justify-between gap-x-3">
        <p className="text-[13px] text-ink-soft">{picturesLabel(card.images)}</p>
        <DeleteButton label={`Удалить съёмку от ${dateLabel(card.created_at)}`} onDelete={onDelete} />
      </div>
    </li>
  );
}

export function My() {
  const { nickname, setNickname } = useSession();
  const navigate = useNavigate();
  const [cards, setCards] = useState<ShootCard[] | null>(null);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setProblem(null);
    try {
      const page = await api.listShoots();
      setCards(page);
      setMore(hasMore(page));
    } catch {
      setProblem(OFFLINE);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const working = cards?.some(cardWorking) ?? false;
  useEffect(() => {
    if (!working) return;
    const refresh = () => {
      if (document.hidden) return;
      // shortcut: a quiet failure is retried on the next tick, this view has no offline note of its own.
      api
        .listShoots()
        .then((fresh) => setCards((all) => all && mergeCards(all, fresh)))
        .catch(() => {});
    };
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [working]);

  async function showMore() {
    setBusy(true);
    setProblem(null);
    try {
      const page = await api.listShoots(cards?.at(-1)?.created_at);
      setCards((all) => [...(all ?? []), ...page]);
      setMore(hasMore(page));
    } catch {
      setProblem(OFFLINE);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setProblem(null);
    try {
      await api.deleteShoot(id);
    } catch (e) {
      // 404: another tab already removed it, the card should go all the same.
      if (!(e instanceof HttpError && e.status === 404)) return setProblem(deleteErrorText(e));
    }
    setCards((all) => all && all.filter((c) => c.id !== id));
  }

  async function leave() {
    setProblem(null);
    try {
      await api.logout();
      setNickname(null);
      setCards(null);
      await load();
      navigate("/my", { replace: true });
    } catch {
      setProblem(OFFLINE);
    }
  }

  return (
    <section className={`${SHELL} flex-1 pt-3 pb-16 lg:pt-8 lg:pb-24`}>
      <title>Мои снимки</title>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <Title className="text-[3.3rem] leading-[0.95] font-medium text-wine lg:text-8xl">
          Мои <em className="text-rosewood">снимки</em>
        </Title>
        {nickname && (
          <p className="flex flex-wrap items-center gap-x-4 text-[15px]">
            <span className="break-all">{nickname}</span>
            <button type="button" onClick={() => void leave()} className={`min-h-11 text-ink-soft ${LINK}`}>
              Выйти
            </button>
          </p>
        )}
      </div>

      {nickname === null && (
        <p className="mt-6 max-w-2xl border-l border-wine pl-4 leading-relaxed">
          История живёт в этом браузере и хранится 30 дней после последнего визита. Ник и пароль сохранят её надолго.{" "}
          <Link to="/login" state={{ from: "/my" }} className={`text-wine ${LINK}`}>
            Войти или создать аккаунт
          </Link>
        </p>
      )}

      {problem && (
        <p role="alert" className={`mt-5 ${ALERT}`}>
          {problem}
        </p>
      )}
      {cards === null && problem && (
        <button type="button" onClick={() => void load()} className={`${BUTTON_QUIET} mt-4`}>
          Ещё раз
        </button>
      )}

      {cards?.length === 0 && (
        <div className="mt-10 grid gap-8 border-t border-line pt-10 lg:mt-16 lg:grid-cols-12 lg:gap-8">
          <PhotoPair urls={["/examples/b-him.jpg", "/examples/b-her.jpg"]} seal className="aspect-[8/7] lg:col-span-4" />
          <div className="lg:col-span-6 lg:col-start-6 lg:self-center">
            <p className="font-display text-[2.5rem] leading-[1.05] text-wine lg:text-6xl">Здесь пока пусто</p>
            <p className="mt-3 leading-relaxed text-ink-soft">Ваш первый снимок начинается с двух портретов. Эти созданы нейросетью для примера.</p>
            <Link to="/" className={`${BUTTON} mt-6 w-full sm:w-auto`}>
              Сделать первый снимок
            </Link>
          </div>
        </div>
      )}

      {cards && (
        <p role="status" className="sr-only">
          Готово снимков: {cards.reduce((sum, card) => sum + card.images, 0)}
        </p>
      )}
      {cards && cards.length > 0 && (
        <ul className="mt-8 grid grid-cols-2 gap-x-3 gap-y-8 lg:mt-14 lg:grid-cols-4 lg:gap-x-8 lg:gap-y-14">
          {cards.map((card) => (
            <Card key={card.id} card={card} onDelete={() => void remove(card.id)} />
          ))}
        </ul>
      )}

      {more && (
        <button type="button" disabled={busy} onClick={() => void showMore()} className={`${BUTTON_QUIET} mx-auto mt-10 flex w-full sm:w-auto lg:mt-14`}>
          Показать ещё
        </button>
      )}
    </section>
  );
}
