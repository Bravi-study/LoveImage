import { Link } from "react-router";

import { BUTTON, LINK, SHELL, Title } from "../ui";

const FACTS = [
  { title: "Зачем нужны фото", text: "Только чтобы сделать совместный снимок. Ни для чего другого мы их не используем." },
  { title: "Кто их видит", text: "На нашем сайте загруженные фото и готовые снимки открываются только их владельцу." },
  { title: "Куда они передаются", text: "Для проверки и создания снимка фото отправляются внешним сервисам искусственного интеллекта: kie.ai и CommandCode. Эти сервисы могут находиться за рубежом и хранят полученные копии по своим правилам, некоторое время после обработки." },
  { title: "Если фото отклонены", text: "Снимки, не прошедшие проверку, сразу удаляются с нашего сервера." },
  { title: "Как удалить", text: "Съёмку можно удалить на её странице или в кабинете, когда снимок не создаётся в эту минуту. Удаляются копии на нашем сервере: у внешних сервисов их удалить мы не можем." },
  { title: "Гости без аккаунта", text: "Данные гостя удаляются через 30 дней без посещений." },
  { title: "Только для взрослых", text: "Сайт рассчитан на людей старше 18 лет. Мы не создаём откровенных изображений и снимков несовершеннолетних." },
];

export function Privacy() {
  return (
    <section className={`${SHELL} flex-1 pt-3 pb-16 lg:grid lg:grid-cols-12 lg:gap-8 lg:pt-10 lg:pb-28`}>
      <title>Как мы обращаемся с фото</title>
      <Title className="text-[3.3rem] leading-[0.95] font-medium text-wine lg:sticky lg:top-8 lg:col-span-5 lg:self-start lg:text-8xl">
        Как мы
        <br />
        обращаемся
        <br />
        <em className="text-rosewood">с фото</em>
      </Title>
      <dl className="mt-8 border-t border-line lg:col-span-6 lg:col-start-7 lg:mt-3">
        {FACTS.map((fact) => (
          <div key={fact.title} className="border-b border-line py-5 lg:py-7">
            <dt className="font-display text-[1.75rem] leading-tight font-medium text-wine">{fact.title}</dt>
            <dd className="mt-1.5 leading-relaxed">{fact.text}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function NotFound() {
  return (
    <section className={`${SHELL} flex flex-1 flex-col justify-center py-16 lg:py-24`}>
      <title>Страница не найдена</title>
      <Title className="text-[3.3rem] leading-[0.95] font-medium text-wine lg:text-8xl">
        Этого кадра <em className="text-rosewood">у нас нет</em>
      </Title>
      <p className="mt-4 max-w-xl text-lg leading-relaxed text-pretty">Такой страницы не существует. Возможно, адрес набран с ошибкой.</p>
      <p className="mt-7 flex flex-wrap items-center gap-x-6 gap-y-3">
        <Link to="/" className={`${BUTTON} w-full sm:w-auto`}>
          На главную
        </Link>
        <Link to="/my" className={`text-wine ${LINK}`}>
          Мои снимки
        </Link>
      </p>
    </section>
  );
}
