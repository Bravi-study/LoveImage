import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";

import * as api from "../api";
import { loginErrorText, registerError } from "../errors";
import { useSession } from "../Layout";
import { ALERT, BUTTON, Title } from "../ui";

const FIELD = "mt-1.5 block min-h-12 w-full border-b border-wine/50 bg-transparent py-2 text-lg";
const MODES = { login: "Вход", register: "Новый аккаунт" } as const;

export function Login() {
  const { nickname, setNickname } = useSession();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/my";
  const [mode, setMode] = useState<keyof typeof MODES>("login");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const registering = mode === "register";

  // Also the way out after a success: from now on this visitor has a nickname.
  if (nickname) return <Navigate to={from} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const clean = name.trim();
    const invalid = registering ? registerError(clean, password) : null;
    setError(invalid);
    if (invalid) return;
    setBusy(true);
    try {
      const me = await (registering ? api.register : api.login)(clean, password);
      setNickname(me.nickname);
    } catch (err) {
      setError(loginErrorText(err));
      setBusy(false);
    }
  }

  return (
    <section className="grid flex-1 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <title>Вход</title>
      <div className="px-5 pt-3 pb-14 lg:px-12 lg:pt-14 lg:pb-24">
        <div className="mx-auto max-w-md lg:mr-0 lg:ml-auto">
          <Title className="text-[3.3rem] leading-[0.95] font-medium text-wine lg:text-[5rem]">
            Ваши снимки
            <br />
            <em className="text-rosewood">при вас</em>
          </Title>
          <p className="mt-4 leading-relaxed text-ink-soft">Только ник и пароль. Всё, что вы уже сделали в этом браузере, останется с вами.</p>

          <div role="group" aria-label="Что сделать" className="mt-8 grid grid-cols-2 border-b border-line">
            {(Object.keys(MODES) as (keyof typeof MODES)[]).map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={mode === key}
                onClick={() => {
                  setMode(key);
                  setError(null);
                }}
                className={`-mb-px min-h-12 border-b-2 text-left font-display text-[1.5rem] transition-colors ${mode === key ? "border-wine text-wine" : "border-transparent text-ink-soft hover:text-wine"}`}
              >
                {MODES[key]}
              </button>
            ))}
          </div>

          <form noValidate onSubmit={(e) => void submit(e)} className="mt-6 flex flex-col gap-5">
            <div>
              <label htmlFor="nickname" className="text-sm font-medium">
                Ник
              </label>
              <input id="nickname" value={name} onChange={(e) => setName(e.target.value)} aria-describedby="nickname-rule" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} className={FIELD} />
              <p id="nickname-rule" className="mt-1.5 text-[13px] text-ink-soft">
                От 3 до 20 букв или цифр, латиницей или кириллицей.
              </p>
            </div>
            <div>
              <label htmlFor="password" className="text-sm font-medium">
                Пароль
              </label>
              <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} aria-describedby="password-rule" autoComplete={registering ? "new-password" : "current-password"} className={FIELD} />
              <p id="password-rule" className="mt-1.5 text-[13px] text-ink-soft">
                От 6 до 128 символов.{registering && " Восстановить пароль нельзя, сохраните его."}
              </p>
            </div>
            {error && (
              <p role="alert" className={ALERT}>
                {error}
              </p>
            )}
            <button type="submit" disabled={busy} className={`${BUTTON} mt-1 w-full`}>
              {registering ? "Создать аккаунт" : "Войти"}
            </button>
          </form>
        </div>
      </div>
      <figure className="relative hidden overflow-hidden lg:block">
        <img src="/examples/b-snow.jpg" alt="Сгенерированный снимок: вымышленная пара, лоб ко лбу, на заснеженной площади старого города" className="absolute inset-0 size-full object-cover object-[50%_30%]" />
        <figcaption className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-wine-deep/80 to-transparent px-8 pt-16 pb-6 text-[13px] text-ivory">Сгенерированный снимок вымышленных людей</figcaption>
      </figure>
    </section>
  );
}
