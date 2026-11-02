import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import * as api from "../api";
import { ACCEPT, checkFile, PHOTO_LABEL, uploadProblems, type PhotoField } from "../errors";
import { useSession, type Draft } from "../Layout";
import { ALERT, BUTTON, CloseIcon, LINK, PlusIcon, Seal, SHELL, Title } from "../ui";

const MAX_WISH = 300;
const WISHES = ["Париж вечером", "Свадьба в лесу", "Чёрно-белое кино 50-х", "Снежный вечер", "Море на закате", "Уютный дом"];
const TIPS = ["Одно лицо в кадре", "Достаточно близко", "Хороший свет", "Без тёмных очков"];
const OTHER: Record<PhotoField, PhotoField> = { photo1: "photo2", photo2: "photo1" };
const SLOTS = [
  { field: "photo1", side: "left", portrait: "/examples/a-him.jpg" },
  { field: "photo2", side: "right", portrait: "/examples/a-her.jpg" },
] as const;

function Preview({ file, label }: { file: File; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    setBroken(false);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  // Most browsers cannot show HEIC. The server can read it, so the photo still counts.
  if (broken) {
    return (
      <div className="absolute inset-0 flex flex-col justify-center gap-2 bg-blush-soft p-4 pb-28 text-center text-sm text-wine">
        <p className="font-medium break-words">{file.name}</p>
        <p>Браузер не показывает этот формат, но снимок загрузится.</p>
      </div>
    );
  }
  return url && <img src={url} alt={`Выбранное фото: ${label}`} onError={() => setBroken(true)} className="absolute inset-0 size-full object-cover object-[50%_30%]" />;
}

type SlotProps = (typeof SLOTS)[number] & {
  file: File | null;
  otherFile: File | null;
  problem?: string;
  onFiles: (files: File[]) => void;
  onRemove: () => void;
};

function Slot({ field, side, portrait, file, otherFile, problem, onFiles, onRemove }: SlotProps) {
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const label = PHOTO_LABEL[field];
  const demo = !file && !otherFile;
  const ghost = !file && !!otherFile;
  const light = ghost && !dragging;

  const drop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
    if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]);
  };
  // relatedTarget stays inside while the drag only crosses a child.
  const leave = (e: DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={leave}
      onDrop={drop}
      className={`slot relative min-h-0 overflow-hidden ${ghost ? "bg-blush-soft" : "bg-wine-deep"} ${demo ? (side === "left" ? "anim-meet-left" : "anim-meet-right") : ""}`}
    >
      {file ? (
        <Preview file={file} label={label} />
      ) : (
        <>
          {/* Twice as wide as its half: the two cards hold the two halves of one example picture. */}
          <img src="/examples/a-paris.jpg" alt="" className={`absolute top-0 h-full w-[200%] max-w-none object-cover object-[50%_12%] ${side === "left" ? "left-0" : "right-0"} ${ghost ? "ghost" : ""}`} />
          {demo && <img src={portrait} alt="" className="anim-portrait absolute inset-0 size-full object-cover object-[50%_30%]" />}
        </>
      )}
      {!light && <span aria-hidden className={`absolute inset-0 bg-gradient-to-t from-wine-deep/85 via-wine-deep/10 via-40% to-transparent transition-opacity ${dragging ? "opacity-100" : "opacity-90"}`} />}
      {dragging && <span aria-hidden className="absolute inset-3 border border-ivory" />}

      <label className={`absolute inset-0 flex cursor-pointer flex-col justify-end p-4 lg:p-8 ${light ? "text-wine" : "text-ivory"}`}>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          aria-describedby={problem ? `${field}-problem` : undefined}
          onChange={(e) => {
            if (e.target.files?.length) onFiles([...e.target.files]);
            // The same file can be picked again after it was removed.
            e.target.value = "";
          }}
        />
        <span className="font-display text-[3.5rem] leading-none italic lg:text-[5.5rem]">{label}</span>
        <span className="mt-2 inline-flex items-center gap-1.5 text-sm font-medium underline-offset-4 hover:underline lg:text-[15px]">
          {dragging ? (
            "Отпустите здесь"
          ) : file ? (
            "Заменить фото"
          ) : (
            <>
              <PlusIcon />
              Добавить фото
            </>
          )}
        </span>
      </label>

      {file && (
        <button
          type="button"
          onClick={() => {
            onRemove();
            // The button is about to disappear, and focus must not fall back to the top of the page.
            input.current?.focus();
          }}
          aria-label={`Убрать фото: ${label}`} className="absolute top-2 right-2 grid size-11 place-items-center rounded-full bg-ivory/90 text-wine transition-colors hover:bg-ivory lg:top-4 lg:right-4"
        >
          <CloseIcon />
        </button>
      )}
    </div>
  );
}

export function Studio() {
  const { draft, setDraft } = useSession();
  const navigate = useNavigate();
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [jump, setJump] = useState(0);
  const { photo1, photo2, wish, problems } = draft;
  const both = !!photo1 && !!photo2;
  const one = !!photo1 !== !!photo2;

  // The problem can be far from the button on a phone. Also runs on arrival: a rejected shoot sends the visitor back here.
  useEffect(() => {
    document.querySelector("[data-problem]")?.scrollIntoView({ block: "nearest" });
  }, [jump]);

  const place = (field: PhotoField, files: File[]) =>
    setDraft((d) => {
      const next: Draft = { ...d, problems: { ...d.problems, form: undefined } };
      const put = (target: PhotoField, file: File) => {
        const bad = checkFile(file);
        next.problems[target] = bad ?? undefined;
        if (!bad) next[target] = file;
      };
      put(field, files[0]);
      // Two files dragged together are the two photos.
      if (files[1] && !d[OTHER[field]]) put(OTHER[field], files[1]);
      return next;
    });

  const setWish = (value: string) => setDraft((d) => ({ ...d, wish: value, problems: { ...d.problems, wish: undefined } }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const missing = {
      photo1: photo1 ? undefined : "Добавьте это фото.",
      photo2: photo2 ? undefined : "Добавьте это фото.",
      consent: consent ? undefined : "Без этого подтверждения мы не можем начать.",
    };
    if (!photo1 || !photo2 || !consent) {
      setDraft((d) => ({ ...d, problems: { ...d.problems, ...missing, form: undefined } }));
      setJump((n) => n + 1);
      return;
    }
    setBusy(true);
    setDraft((d) => ({ ...d, problems: {} }));
    const form = new FormData();
    form.set("photo1", photo1);
    form.set("photo2", photo2);
    form.set("wish", wish);
    form.set("consent", "true");
    try {
      const shoot = await api.createShoot(form);
      navigate(`/shoot/${shoot.id}`, { state: { shoot } });
    } catch (err) {
      setDraft((d) => ({ ...d, problems: uploadProblems(err) }));
      setJump((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <title>LoveImage: две фотографии, один кадр</title>
      <form id="studio" noValidate onSubmit={(e) => void submit(e)} className="grid scroll-mt-20 lg:grid-cols-[minmax(0,1fr)_52vw] lg:grid-rows-[auto_1fr]">
        <div className="px-5 pt-3 pb-6 lg:pt-6 lg:pr-14 lg:pb-0 lg:pl-12">
          <Title className="text-[3.3rem] leading-[0.94] font-medium tracking-[-0.015em] text-wine sm:text-7xl lg:text-[clamp(4.5rem,6.6vw,7.25rem)]">
            Две фотографии.
            <br />
            <em className="text-rosewood">Один кадр.</em>
          </Title>
        </div>

        <div className="min-w-0 lg:col-start-2 lg:row-span-2 lg:row-start-1">
          <div className={`relative grid aspect-[8/9] grid-cols-2 transition-[gap] duration-500 lg:aspect-auto lg:h-[min(calc(100dvh-4.5rem),60rem)] ${one ? "gap-2 lg:gap-3" : "gap-px"}`}>
            {SLOTS.map((slot) => (
              <Slot
                key={slot.field}
                {...slot}
                file={draft[slot.field]}
                otherFile={draft[OTHER[slot.field]]}
                problem={problems[slot.field]}
                onFiles={(files) => place(slot.field, files)}
                onRemove={() => setDraft((d) => ({ ...d, [slot.field]: null, problems: { ...d.problems, [slot.field]: undefined } }))}
              />
            ))}
            {!one && <Seal className="anim-seal absolute top-[60%] left-1/2 size-12 -translate-x-1/2 -translate-y-1/2 text-[1.7rem] lg:size-16 lg:text-[2.25rem]" />}
          </div>

          {(problems.photo1 || problems.photo2) && (
            <div className="grid grid-cols-2 gap-x-3 px-5 pt-3 lg:px-8">
              {SLOTS.map(({ field }) => (
                <div key={field}>
                  {problems[field] && (
                    <p id={`${field}-problem`} role="alert" data-problem className={ALERT}>
                      {problems[field]}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="px-5 pt-3 lg:px-8 lg:pt-5 lg:pb-8">
            <p className="text-[13px] leading-relaxed text-ink-soft">
              {photo1 || photo2 ? "Снимки уйдут на сервер только после нажатия кнопки." : "На примере: сгенерированный снимок вымышленных людей. Положите на его место свои фото."}
            </p>
            <h2 className="mt-4 font-display text-2xl leading-tight text-wine italic">Какое фото подойдёт</h2>
            <ul className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-sm lg:grid-cols-4">
              {TIPS.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
            </ul>
          </div>
        </div>

        <div className="min-w-0 px-5 pt-7 pb-14 lg:pt-6 lg:pr-14 lg:pb-16 lg:pl-12">
          <p className="max-w-[34rem] text-[17px] leading-relaxed text-ink">Добавьте по одному портрету каждого из вас. Нейросеть соберёт из них совместный снимок: вы вдвоём там, где захотите оказаться.</p>

          <div className="mt-6 flex items-baseline justify-between gap-4">
            <label htmlFor="wish" className="font-display text-[1.75rem] leading-none text-wine italic">
              Пожелание
            </label>
            <span className="text-[13px] text-ink-soft">можно пропустить</span>
          </div>
          <textarea
            id="wish"
            rows={2}
            maxLength={MAX_WISH}
            value={wish}
            onChange={(e) => setWish(e.target.value)}
            placeholder="Где вы, какой свет, какое настроение?"
            aria-describedby={`wish-count${problems.wish ? " wish-problem" : ""}`}
            aria-invalid={!!problems.wish}
            className="mt-2 block min-h-[5.25rem] w-full resize-none border-b border-wine/50 bg-transparent py-2 font-display text-[1.4rem] leading-snug field-sizing-content placeholder:text-ink-soft"
          />
          <p id="wish-count" className="mt-1.5 text-right text-[13px] text-ink-soft tabular-nums">
            {wish.length} из {MAX_WISH}
          </p>
          {problems.wish && (
            <p id="wish-problem" role="alert" data-problem className={`mt-1 ${ALERT}`}>
              {problems.wish}
            </p>
          )}

          <div role="group" aria-label="Готовые пожелания" className="mt-2 flex flex-wrap gap-2">
            {WISHES.map((text) => (
              <button
                key={text}
                type="button"
                aria-pressed={wish === text}
                onClick={() => setWish(wish === text ? "" : text)}
                className={`min-h-10 rounded-full border px-3.5 text-sm transition-colors ${wish === text ? "border-wine bg-wine text-ivory" : "border-wine/30 hover:border-wine hover:bg-blush-soft"}`}
              >
                {text}
              </button>
            ))}
          </div>

          <label className="mt-7 flex gap-3 text-sm leading-relaxed">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => {
                setConsent(e.target.checked);
                setDraft((d) => ({ ...d, problems: { ...d.problems, consent: undefined } }));
              }}
              aria-describedby={problems.consent ? "consent-problem" : undefined}
              className="mt-0.5 size-5 shrink-0 accent-wine"
            />
            <span>
              Подтверждаю: оба человека на этих фото взрослые, и оба согласны на обработку фото и создание совместного снимка.{" "}
              <Link to="/privacy" className={`whitespace-nowrap text-wine ${LINK}`}>
                Как мы обращаемся с фото
              </Link>
            </span>
          </label>
          {problems.consent && (
            <p id="consent-problem" role="alert" data-problem className={`mt-2 ${ALERT}`}>
              {problems.consent}
            </p>
          )}

          {problems.form && (
            <p role="alert" data-problem className={`mt-5 ${ALERT}`}>
              {problems.form}
            </p>
          )}

          <button type="submit" disabled={busy} className={`${BUTTON} mt-6 w-full`}>
            {busy ? "Загружаем снимки…" : both ? "Сделать наш снимок" : "Сделать снимок"}
          </button>
          <p role="status" className="mt-3 text-center text-[13px] text-ink-soft">
            {busy ? "Большие фото могут загружаться около минуты." : "Обычно это занимает от 1 до 3 минут."}
          </p>
        </div>
      </form>

      <HowItWorks />
      <Gallery />
      <section className="bg-blush-soft">
        <div className={`${SHELL} flex flex-col gap-6 py-16 lg:flex-row lg:items-end lg:justify-between lg:py-24`}>
          <h2 className="font-display text-5xl leading-[0.98] font-medium text-wine lg:text-7xl">
            Начните
            <br />
            <em className="text-rosewood">с двух снимков</em>
          </h2>
          <a href="#studio" className={`${BUTTON} lg:min-w-72`}>
            Добавить фото
          </a>
        </div>
      </section>
    </>
  );
}

const STEPS = [
  { title: "Два портрета", text: "По одному снимку на каждого. Подойдут JPEG, PNG, WEBP и HEIC, до 25 МБ каждый." },
  { title: "Пожелание", text: "Место, эпоха, время года, настроение. Можно ничего не писать, тогда сцену выберем сами." },
  { title: "Ваш снимок", text: "Обычно через 1–3 минуты, иногда до 7. Страницу можно закрыть: снимок будет ждать в кабинете. Понравится не всё — попросите ещё вариант." },
];

function HowItWorks() {
  return (
    <section className={`${SHELL} py-16 lg:grid lg:grid-cols-12 lg:gap-8 lg:py-28`}>
      <h2 className="font-display text-5xl leading-[0.98] font-medium text-wine lg:col-span-4 lg:text-7xl">
        Как это
        <br />
        <em className="text-rosewood">устроено</em>
      </h2>
      <ol className="mt-8 border-t border-line lg:col-span-7 lg:col-start-6 lg:mt-3">
        {STEPS.map((step, i) => (
          <li key={step.title} className="grid grid-cols-[2.75rem_minmax(0,1fr)] gap-x-2 border-b border-line py-6 lg:grid-cols-[5rem_minmax(0,14rem)_minmax(0,1fr)] lg:gap-x-6 lg:py-9">
            <span aria-hidden className="font-display text-[2.75rem] leading-none text-rosewood italic [font-variant-numeric:lining-nums] lg:text-6xl">
              {i + 1}
            </span>
            <h3 className="font-display text-[1.9rem] leading-tight font-medium text-wine">{step.title}</h3>
            <p className="col-start-2 mt-2 leading-relaxed text-ink-soft lg:col-start-3 lg:mt-1">{step.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Shot({ name, wish, alt, className = "" }: { name: string; wish: string; alt: string; className?: string }) {
  return (
    <figure className={`min-w-0 ${className}`}>
      <img src={`/examples/${name}.jpg`} alt={alt} loading="lazy" width={750} height={1000} className="aspect-[3/4] w-full object-cover" />
      <figcaption className="mt-2.5 font-display text-xl leading-snug text-blush italic lg:text-2xl">{wish}</figcaption>
    </figure>
  );
}

function Sources({ names, className = "" }: { names: [string, string]; className?: string }) {
  return (
    <figure className={`min-w-0 ${className}`}>
      <div className="grid grid-cols-2 gap-px">
        {names.map((name) => (
          <img key={name} src={`/examples/${name}.jpg`} alt="Исходный портрет вымышленного человека, созданный нейросетью" loading="lazy" width={750} height={1000} className="aspect-[3/4] w-full object-cover" />
        ))}
      </div>
      <figcaption className="mt-2.5 text-sm leading-snug text-blush">Было: два обычных портрета</figcaption>
    </figure>
  );
}

function Gallery() {
  return (
    <section aria-labelledby="gallery" className="on-dark bg-wine text-ivory">
      <div className={`${SHELL} py-16 lg:py-28`}>
        <div className="lg:grid lg:grid-cols-12 lg:items-end lg:gap-8">
          <h2 id="gallery" className="font-display text-5xl leading-[0.98] font-medium lg:col-span-7 lg:text-8xl">
            Что <em className="text-blush">получается</em>
          </h2>
          <p className="mt-4 leading-relaxed text-blush lg:col-span-4 lg:col-start-9 lg:mt-0">Все снимки и портреты на этой странице созданы нейросетью. Этих людей не существует.</p>
        </div>

        <div className="mt-10 grid grid-cols-2 gap-x-3 gap-y-8 lg:mt-16 lg:grid-cols-12 lg:gap-x-8 lg:gap-y-10">
          <Sources names={["a-him", "a-her"]} className="col-span-2 lg:col-span-3 lg:col-start-1 lg:row-start-1" />
          <Shot name="a-noir" wish="Чёрно-белое кино 50-х" alt="Сгенерированный снимок: вымышленная пара за столиком кафе, чёрно-белый кадр в духе кино 1950-х" className="col-span-2 lg:col-span-5 lg:col-start-4 lg:row-span-2 lg:row-start-1" />
          <Shot name="a-rain" wish="Вечер под дождём" alt="Сгенерированный снимок: та же пара под одним зонтом на вечерней улице в дождь" className="lg:col-span-4 lg:col-start-9 lg:row-span-2 lg:row-start-1 lg:mt-24" />
          <Shot name="a-paris" wish="Париж вечером" alt="Сгенерированный снимок: та же пара на набережной в Париже на закате" className="lg:col-span-3 lg:col-start-1 lg:row-start-2 lg:self-end" />

          <Sources names={["b-him", "b-her"]} className="col-span-2 mt-6 lg:col-span-3 lg:col-start-10 lg:row-start-3 lg:mt-16" />
          <Shot name="b-snow" wish="Снежный вечер" alt="Сгенерированный снимок: другая вымышленная пара, лоб ко лбу, на заснеженной площади старого города" className="col-span-2 lg:col-span-5 lg:col-start-5 lg:row-span-2 lg:row-start-3 lg:mt-16" />
          <Shot name="b-wedding" wish="Свадьба в лесу" alt="Сгенерированный снимок: та же пара в свадебных нарядах на лесной поляне" className="lg:col-span-4 lg:col-start-1 lg:row-span-2 lg:row-start-3 lg:mt-40" />
          <Shot name="b-sea" wish="Море на закате" alt="Сгенерированный снимок: та же пара на берегу моря на закате" className="lg:col-span-3 lg:col-start-10 lg:row-start-4 lg:self-end" />
        </div>
      </div>
    </section>
  );
}
