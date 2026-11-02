import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import * as api from "../api";
import type { Job, Shoot } from "../api";
import { deleteErrorText, jobErrorText, orderErrorText, PHOTO_LABEL, rejectProblems, rejectText } from "../errors";
import { useSession } from "../Layout";
import { isActive, pickView, statusText } from "../shoots";
import { ALERT, BUTTON, BUTTON_QUIET, CheckIcon, DeleteButton, DownloadIcon, LINK, PhotoPair, SHELL, Title } from "../ui";
import { useShoot } from "../useShoot";

const SOURCE_ALTS = [`Исходное фото: ${PHOTO_LABEL.photo1}`, `Исходное фото: ${PHOTO_LABEL.photo2}`];
const PRINT_H = "lg:h-[min(calc(100dvh-9rem),60rem)]";
const WAIT_NOTE = "Обычно снимок готов через 1–3 минуты, иногда через 7. Эту страницу можно закрыть: он будет ждать в кабинете.";

/** The waiting room: the visitor's own two photos under a slow safelight, for a wait that can last minutes. */
function Darkroom({ shoot, stage }: { shoot: Shoot; stage: "check" | "make" }) {
  const checking = stage === "check";
  const steps = [
    { name: "Проверяем снимки", done: !checking, current: checking },
    { name: "Создаём ваш снимок", done: false, current: !checking },
  ];
  return (
    <section className="on-dark relative flex flex-1 overflow-hidden bg-wine-deep text-ivory">
      <span aria-hidden className="anim-glow absolute inset-0 bg-[radial-gradient(70%_60%_at_32%_42%,rgba(150,50,62,0.55),transparent_75%)]" />
      <div className={`${SHELL} relative grid items-center gap-9 py-8 lg:grid-cols-12 lg:gap-8 lg:py-16`}>
        <PhotoPair urls={shoot.photo_urls} alts={SOURCE_ALTS} drift imgClassName="anim-develop" className="aspect-[8/7] gap-1.5 bg-transparent lg:col-span-6 lg:col-start-1 lg:aspect-[8/9] lg:gap-2" />
        <div className="lg:col-span-5 lg:col-start-8">
          <Title className="text-[2.9rem] leading-[1] font-medium lg:text-7xl">{checking ? "Проверяем снимки" : "Создаём ваш снимок"}</Title>
          {shoot.wish && <p className="mt-4 font-display text-[1.6rem] leading-snug text-blush italic lg:text-3xl">«{shoot.wish}»</p>}
          <ol className="mt-7 border-t border-ivory/20 lg:mt-10">
            {steps.map((step) => (
              <li key={step.name} aria-current={step.current ? "step" : undefined} className={`flex items-center gap-3.5 border-b border-ivory/20 py-3.5 ${step.done || step.current ? "" : "text-blush/70"}`}>
                <span aria-hidden className="grid size-4 place-items-center">
                  {step.done ? <CheckIcon /> : <span className={`size-2 rounded-full ${step.current ? "anim-pulse-dot bg-blush" : "border border-blush/60"}`} />}
                </span>
                {step.name}
              </li>
            ))}
          </ol>
          <p className="mt-6 leading-relaxed text-blush">{WAIT_NOTE}</p>
          <Link to="/my" className={`mt-4 inline-flex min-h-11 items-center text-ivory ${LINK} hover:text-blush`}>
            Перейти в кабинет
          </Link>
        </div>
      </div>
    </section>
  );
}

/** A plain centred note for the states that ask for one decision. */
function Note({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className={`${SHELL} flex flex-1 flex-col justify-center py-14 lg:py-24`}>
      <div className="max-w-2xl">
        <Title className="text-[2.75rem] leading-[1.02] font-medium text-wine lg:text-7xl">{title}</Title>
        {children}
      </div>
    </section>
  );
}

function Rejected({ reason }: { reason: string | null }) {
  const { draft, setDraft } = useSession();
  const { title, body } = rejectText(reason);
  const kept = !!draft.photo1 || !!draft.photo2;
  return (
    <Note title={title}>
      <p className="mt-5 text-lg leading-relaxed">{body}</p>
      <p className="mt-4 leading-relaxed text-ink-soft">
        С сервера эти снимки уже удалены.
        {kept ? " В форме они остались, пока открыта эта вкладка: добавлять их заново не нужно." : " Чтобы попробовать снова, добавьте фото ещё раз."}
      </p>
      <Link to="/" onClick={() => setDraft((d) => ({ ...d, problems: rejectProblems(reason) }))} className={`${BUTTON} mt-8 w-full sm:w-auto`}>
        {kept ? "Вернуться к форме" : "Загрузить снимки"}
      </Link>
    </Note>
  );
}

function Variants({ jobs, current, onPick }: { jobs: Job[]; current: number; onPick: (variant: number) => void }) {
  return (
    <div role="group" aria-label="Варианты" className="mt-8">
      <h2 className="font-display text-2xl text-wine italic">Варианты</h2>
      <ul className="mt-2 flex flex-wrap gap-2">
        {jobs.map((job) => (
          <li key={job.variant}>
            <button
              type="button"
              aria-pressed={job.variant === current}
              aria-label={`Вариант ${job.variant}${isActive(job) ? ", создаётся" : job.status === "failed" ? ", не получился" : ""}`}
              onClick={() => onPick(job.variant)}
              className={`relative block h-[5.5rem] w-[4.25rem] overflow-hidden bg-blush-soft ${job.variant === current ? "outline-2 outline-offset-2 outline-wine" : "opacity-85 hover:opacity-100"}`}
            >
              {job.result_url ? (
                <img src={job.result_url} alt="" className="size-full object-cover" />
              ) : (
                <span className="grid size-full place-items-center font-display text-3xl text-wine italic">
                  {isActive(job) ? <span className="anim-pulse-dot size-2 rounded-full bg-wine" /> : job.variant}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ShootScreen() {
  const { id = "" } = useParams();
  const first = (useLocation().state as { shoot?: Shoot } | null)?.shoot;
  const navigate = useNavigate();
  const { shoot, setShoot, reload, missing, offline } = useShoot(id, first);
  const [selected, setSelected] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [ordering, setOrdering] = useState(false);

  async function order(variant?: number) {
    setOrdering(true);
    setProblem(null);
    try {
      setShoot(await api.orderJob(id, variant));
      setSelected(variant ?? null);
    } catch (e) {
      setProblem(orderErrorText(e));
    } finally {
      setOrdering(false);
    }
  }

  async function recheck() {
    setOrdering(true);
    setProblem(null);
    try {
      setShoot(await api.repeatAnalysis(id));
    } catch {
      setProblem("Не получилось начать проверку. Попробуйте ещё раз.");
      void reload();
    } finally {
      setOrdering(false);
    }
  }

  async function remove() {
    setProblem(null);
    try {
      await api.deleteShoot(id);
      navigate("/my");
    } catch (e) {
      setProblem(deleteErrorText(e));
    }
  }

  const alert = problem && (
    <p role="alert" className={`mt-4 ${ALERT}`}>
      {problem}
    </p>
  );

  if (missing) {
    return (
      <Note title="Такой съёмки здесь нет">
        <title>Съёмка не найдена</title>
        <p className="mt-5 text-lg leading-relaxed">Она могла быть удалена, а если она ваша, её можно увидеть только из того браузера или аккаунта, где её сделали.</p>
        <p className="mt-6 flex flex-wrap gap-x-6 gap-y-2">
          <Link to="/my" className={`min-h-11 text-wine ${LINK}`}>
            Мои снимки
          </Link>
          <Link to="/login" state={{ from: `/shoot/${id}` }} className={`min-h-11 text-wine ${LINK}`}>
            Войти
          </Link>
          <Link to="/" className={`min-h-11 text-wine ${LINK}`}>
            Новая съёмка
          </Link>
        </p>
      </Note>
    );
  }

  if (!shoot) {
    return (
      <p role="status" className={`${SHELL} flex-1 py-16 font-display text-3xl text-wine italic`}>
        {offline ? "Нет связи. Пробуем снова…" : "Открываем…"}
      </p>
    );
  }

  const view = pickView(shoot, selected);
  const job = view.name === "pictures" ? view.job : null;
  const running = shoot.jobs.some(isActive);

  return (
    <>
      <title>{view.name === "pictures" && job?.status === "succeeded" ? "Ваш снимок" : view.name === "rejected" ? "Снимки не прошли проверку" : "Ваш снимок создаётся"}</title>
      <p role="status" className="sr-only">
        {statusText(view)}
      </p>
      {offline && (
        <p role="status" className="bg-wine px-5 py-2 text-center text-sm text-ivory">
          Нет связи. Пробуем снова…
        </p>
      )}

      {view.name === "checking" && <Darkroom shoot={shoot} stage="check" />}
      {view.name === "first_picture" && <Darkroom shoot={shoot} stage="make" />}
      {view.name === "rejected" && <Rejected reason={view.reason} />}

      {view.name === "check_failed" && (
        <Note title="Не получилось проверить снимки">
          <p className="mt-5 text-lg leading-relaxed">Это сбой на нашей стороне, ваши фото тут ни при чём. Они на месте, проверку можно повторить.</p>
          <button type="button" disabled={ordering} onClick={() => void recheck()} className={`${BUTTON} mt-8 w-full sm:w-auto`}>
            Проверить ещё раз
          </button>
          {alert}
        </Note>
      )}

      {view.name === "no_jobs" && (
        <Note title="Снимки подошли">
          <p className="mt-5 text-lg leading-relaxed">Когда мы их проверили, дневной лимит уже был исчерпан, поэтому снимок ещё не заказан. Попробуйте сейчас, а если не выйдет, вернитесь завтра.</p>
          <button type="button" disabled={ordering} onClick={() => void order()} className={`${BUTTON} mt-8 w-full sm:w-auto`}>
            Сделать снимок
          </button>
          {alert}
        </Note>
      )}

      {job && (
        <section className={`${SHELL} grid flex-1 gap-7 pt-2 pb-14 lg:grid-cols-[auto_minmax(0,1fr)] lg:gap-20 lg:pt-6 lg:pb-20`}>
          <div className="min-w-0">
            {job.result_url ? (
              <figure className="bg-paper p-2 shadow-[0_18px_50px_-24px_rgba(52,9,18,0.55)] lg:p-3">
                <img src={job.result_url} alt={`Совместный снимок, вариант ${job.variant}`} className={`block aspect-[3/4] w-full object-contain lg:w-auto ${PRINT_H}`} />
              </figure>
            ) : isActive(job) ? (
              <div className={`on-dark relative grid aspect-[3/4] place-items-center overflow-hidden bg-wine-deep text-ivory ${PRINT_H}`}>
                <span aria-hidden className="anim-glow absolute inset-0 bg-[radial-gradient(60%_50%_at_50%_45%,rgba(150,50,62,0.55),transparent_75%)]" />
                <div className="relative w-2/3 max-w-sm text-center">
                  <PhotoPair urls={shoot.photo_urls} drift imgClassName="anim-develop" className="aspect-[8/7] gap-1 bg-transparent" />
                  <p className="mt-6 font-display text-3xl leading-tight text-balance">Создаём вариант {job.variant}</p>
                  <p className="mt-2 text-sm leading-relaxed text-blush">Обычно 1–3 минуты, иногда до 7. Страницу можно закрыть.</p>
                </div>
              </div>
            ) : (
              <div className={`grid aspect-[3/4] place-items-center bg-blush-soft p-6 ${PRINT_H}`}>
                <div className="max-w-md text-center">
                  <p className="font-display text-[2rem] leading-tight text-wine lg:text-5xl">{jobErrorText(job.error)}</p>
                  <button type="button" disabled={ordering || running} onClick={() => void order(job.can_retry ? job.variant : undefined)} className={`${BUTTON} mt-7 w-full sm:w-auto`}>
                    {job.can_retry ? "Попробовать ещё раз" : "Заказать новый вариант"}
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="max-w-lg min-w-0 lg:pt-4">
            <Title className="text-[2.75rem] leading-[1] font-medium text-wine lg:text-7xl">
              {job.status === "succeeded" ? (
                <>
                  Ваш <em className="text-rosewood">снимок</em>
                </>
              ) : job.status === "failed" ? (
                "Вариант не получился"
              ) : (
                "Новый вариант"
              )}
            </Title>
            {shoot.wish && <p className="mt-4 font-display text-[1.6rem] leading-snug text-ink italic">«{shoot.wish}»</p>}

            <div className="mt-7 flex flex-col gap-3">
              {job.result_url && (
                <a href={job.result_url} download={`loveimage-${job.variant}.jpg`} className={BUTTON}>
                  <DownloadIcon />
                  Скачать
                </a>
              )}
              {/* A failed job that cannot repeat already offers a new variant on the print itself. */}
              {(job.status !== "failed" || job.can_retry) && (
                <button type="button" disabled={ordering || running} onClick={() => void order()} className={BUTTON_QUIET}>
                  {running ? "Вариант создаётся…" : "Ещё вариант"}
                </button>
              )}
            </div>
            {alert}

            {shoot.jobs.length > 1 && <Variants jobs={shoot.jobs} current={job.variant} onPick={setSelected} />}

            <div className="mt-8 flex items-end gap-4 border-t border-line pt-5">
              <PhotoPair urls={shoot.photo_urls} alts={SOURCE_ALTS} className="aspect-[4/3] w-28 shrink-0" />
              <div>
                <p className="text-sm leading-snug text-ink-soft">Исходные фото</p>
                <DeleteButton label="Удалить всю съёмку" onDelete={() => void remove()} />
              </div>
            </div>
          </div>
        </section>
      )}
    </>
  );
}
