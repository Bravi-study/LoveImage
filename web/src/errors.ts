import { HttpError } from "./api.ts";

export type PhotoField = "photo1" | "photo2";
/** Labels only: the backend never checks who is on which photo. */
export const PHOTO_LABEL: Record<PhotoField, string> = { photo1: "Он", photo2: "Она" };

export type Problems = Partial<Record<PhotoField | "wish" | "consent" | "form", string>>;

export const MAX_PHOTO = 25 * 1024 * 1024;
export const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.heic,.heif";
const FORMATS = "JPEG, PNG, WEBP или HEIC";
const OFFLINE = "Нет связи с сервером.";
const KEPT = "Снимки и текст на месте, попробуйте ещё раз.";
const NICKNAME = /^[0-9A-Za-zА-Яа-яЁё]{3,20}$/;

/**
 * Every number a visitor can see. What each one means stays in these comments, so it never reaches the page.
 * Three digits instead (502, 413) come from a proxy or the dev server in front of the app.
 */
export const ERROR_NUMBERS = {
  // The app answered with an error: docker compose logs --tail 100 app
  server: {
    other: 10, // not listed here, the log has the full text
    ENOSPC: 11, // no disk space left
    EACCES: 12, // no right to write to the photo folder
    gaierror: 13, // the database is not found: the db container is stopped
  },
  // The photo check could not run: docker compose logs --tail 100 worker
  check: {
    other: 20, // not listed here
    deadline: 21, // no usable answer from the language model for 90 seconds
    llm_401: 22, // CommandCode refused the key
    llm_402: 23, // no money on the CommandCode account
    llm_403: 24, // CommandCode refused access
    internal: 25, // the worker crashed
  },
  // The picture failed: docker compose logs --tail 100 worker
  job: {
    other: 30, // not listed here
    no_credits: 31, // no credits left at kie.ai
    provider_auth: 32, // kie.ai refused the key
    provider_rejected: 33, // kie.ai refused the task
    provider_failed: 34, // the task failed at kie.ai or its result is unusable
    submit_unknown: 35, // no telling whether kie.ai took the task, so it is not repeated by itself
    deadline: 36, // no result in 7 minutes
    download_failed: 37, // the picture is ready but did not download in time
    internal: 38, // the worker crashed
  },
};
const inTable = (table: Record<string, number>, name: string | null) => (name ? ` (ошибка ${table[name] ?? table.other})` : "");
const fromAnswer = (e: HttpError) => (e.detail ? inTable(ERROR_NUMBERS.server, e.detail) : ` (ошибка ${e.status})`);
const failed = (e: HttpError) => `Что-то пошло не так на нашей стороне${fromAnswer(e)}.`;
const tryAgain = (text: string, e: unknown) => `${text}${e instanceof HttpError ? fromAnswer(e) : ""}. Попробуйте ещё раз.`;

const LIMITS: Record<string, string> = {
  ip_limit: "С этого адреса сегодня заказано слишком много снимков. Попробуйте завтра.",
  daily_limit: "На сегодня сервис сделал все снимки, какие мог. Загляните завтра.",
  personal_limit: "Ваш лимит на сегодня исчерпан. Новые снимки можно будет сделать завтра.",
};

/** HEIC often comes with an empty type, so the extension counts too. */
export function checkFile(file: File) {
  if (!/^image\/(jpeg|png|webp|heic|heif)$/.test(file.type) && !/\.(jpe?g|png|webp|hei[cf])$/i.test(file.name)) return `Такой файл не подойдёт. Нужно фото: ${FORMATS}.`;
  if (file.size > MAX_PHOTO) return "Это фото больше 25 МБ. Выберите снимок поменьше.";
  return null;
}

export function uploadProblems(e: unknown): Problems {
  if (!(e instanceof HttpError)) return { form: `${OFFLINE} ${KEPT}` };
  const unreadable = `Этот файл не открывается как фото. Выберите другой: ${FORMATS}.`;
  if (e.detail === "bad_image_1") return { photo1: unreadable };
  if (e.detail === "bad_image_2") return { photo2: unreadable };
  if (e.detail === "bad_wish") return { wish: "Такое пожелание не получается принять: оно длиннее 300 знаков или в нём есть непечатаемые символы." };
  if (e.detail === "too_large") return { form: "Один из снимков больше 25 МБ. Замените его снимком поменьше." };
  if (e.detail === "invalid") return { form: "Нужны оба снимка и отмеченное согласие." };
  if (e.detail && LIMITS[e.detail]) return { form: LIMITS[e.detail] };
  return { form: `${failed(e)} ${KEPT}` };
}

const noFace = (label: string) => ({
  title: `На фото «${label}» не нашлось лица`,
  body: "Нужен снимок, где одно лицо видно крупно и ясно. Возможно, в кадре несколько человек, лицо слишком мелкое или закрыто, или это не фотография.",
});

export function rejectText(reason: string | null) {
  if (reason === "no_face_1") return noFace(PHOTO_LABEL.photo1);
  if (reason === "no_face_2") return noFace(PHOTO_LABEL.photo2);
  if (reason === "minor")
    return {
      title: "Мы работаем только со снимками взрослых",
      body: "Проверка решила, что кто-то на снимках может быть младше 18 лет. Если это ошибка, попробуйте другие снимки, где лицо видно лучше.",
    };
  if (reason === "unsafe_photo")
    return {
      title: "С такими снимками мы не работаем",
      body: "Проверка нашла на одном из них обнажённое тело, сцену насилия или символы ненависти. Выберите другие снимки.",
    };
  if (reason === "famous_person")
    return {
      title: "Похоже, на снимке известный человек",
      body: "Мы делаем снимки только по вашим собственным фото. Если проверка ошиблась, попробуйте другой снимок.",
    };
  if (reason === "photo_text")
    return {
      title: "На снимке есть надпись, похожая на указание",
      body: "Выберите обычный портрет без такой надписи.",
    };
  if (reason === "unsafe_wish")
    return {
      title: "С таким пожеланием мы не работаем",
      body: "Мы не делаем откровенных снимков, сцен с насилием, оружием и наркотиками, а также снимков с известными людьми и персонажами. Напишите иначе: место, эпоха, одежда, погода, настроение. Или оставьте поле пустым.",
    };
  if (reason === "bad_wish")
    return {
      title: "Не получилось понять пожелание",
      body: "Опишите его проще: место, время года, настроение. Например, «Море на закате». Или оставьте поле пустым, тогда сцену выберем сами.",
    };
  return { title: "Снимки не прошли проверку", body: "Попробуйте другие снимки: одно лицо в кадре, крупно и при хорошем свете." };
}

/** Where a rejection shows up on the form, so only what is wrong needs fixing. */
export function rejectProblems(reason: string | null): Problems {
  const { title, body } = rejectText(reason);
  if (reason === "no_face_1") return { photo1: "Здесь не нашлось лица. Замените этот снимок." };
  if (reason === "no_face_2") return { photo2: "Здесь не нашлось лица. Замените этот снимок." };
  if (reason === "unsafe_wish" || reason === "bad_wish") return { wish: `${title}. ${body}` };
  return { form: `${title}. ${body}` };
}

export function jobErrorText(error: string | null) {
  const number = inTable(ERROR_NUMBERS.job, error);
  return error === "no_credits" || error === "provider_auth" ? `Сервис временно недоступен${number}. Попробуйте чуть позже.` : `Этот снимок не получился${number}. Попробуйте ещё раз.`;
}

export const checkErrorText = (error: string | null) => `Это сбой на нашей стороне${inTable(ERROR_NUMBERS.check, error)}, ваши фото тут ни при чём. Они на месте, проверку можно повторить.`;

export function orderErrorText(e: unknown) {
  if (!(e instanceof HttpError)) return `${OFFLINE} Попробуйте ещё раз.`;
  if (e.detail && LIMITS[e.detail]) return LIMITS[e.detail];
  if (e.detail === "not_retryable") return "Этот вариант повторить уже нельзя. Закажите новый.";
  if (e.detail === "not_ready") return "Снимки ещё проверяются. Подождите немного.";
  return `${failed(e)} Попробуйте ещё раз.`;
}

export function deleteErrorText(e: unknown) {
  if (e instanceof HttpError && e.detail === "busy") return "Снимок ещё создаётся. Удалить съёмку можно, когда он будет готов.";
  return tryAgain("Не получилось удалить", e);
}

export const recheckErrorText = (e: unknown) => tryAgain("Не получилось начать проверку", e);

export function registerError(nickname: string, password: string) {
  if (!NICKNAME.test(nickname)) return "В нике нужно от 3 до 20 букв или цифр, латиницей или кириллицей.";
  if (password.length < 6) return "В пароле нужно от 6 символов.";
  if (password.length > 128) return "В пароле может быть не больше 128 символов.";
  return null;
}

export function loginErrorText(e: unknown) {
  if (!(e instanceof HttpError)) return `${OFFLINE} Попробуйте ещё раз.`;
  if (e.detail === "bad_credentials") return "Неверный ник или пароль.";
  if (e.detail === "nickname_taken") return "Такой ник уже занят. Выберите другой или войдите, если он ваш.";
  if (e.detail === "locked") return "Слишком много неудачных попыток. Попробуйте через 15 минут.";
  if (e.detail === "invalid") return "Проверьте ник и пароль: ник от 3 до 20 букв или цифр, пароль от 6 до 128 символов.";
  return `${failed(e)} Попробуйте ещё раз.`;
}
