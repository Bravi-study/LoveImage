import "@fontsource-variable/cormorant/wght.css";
import "@fontsource-variable/cormorant/wght-italic.css";
import "@fontsource-variable/manrope/wght.css";
import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router";

import { Layout } from "./Layout";
import { Login } from "./screens/Login";
import { My } from "./screens/My";
import { NotFound, Privacy } from "./screens/Privacy";
import { ShootScreen } from "./screens/Shoot";
import { Studio } from "./screens/Studio";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter useTransitions={false}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Studio />} />
          <Route path="shoot/:id" element={<ShootScreen />} />
          <Route path="my" element={<My />} />
          <Route path="login" element={<Login />} />
          <Route path="privacy" element={<Privacy />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
