import assert from "node:assert/strict";
import test from "node:test";

import { createShoot, HttpError } from "./api.ts";
import { checkFile, deleteErrorText, jobErrorText, loginErrorText, MAX_PHOTO, orderErrorText, PHOTO_LABEL, registerError, rejectProblems, rejectText, uploadProblems } from "./errors.ts";

const http = (status: number, detail: string | null) => new HttpError(status, detail);
const sized = (name: string, type: string, size: number) => {
  const file = new File(["x"], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
};

test("an upload error lands where it can be fixed", () => {
  assert.deepEqual(Object.keys(uploadProblems(http(422, "bad_image_1"))), ["photo1"]);
  assert.deepEqual(Object.keys(uploadProblems(http(422, "bad_image_2"))), ["photo2"]);
  assert.deepEqual(Object.keys(uploadProblems(http(422, "bad_wish"))), ["wish"]);
  for (const [status, code] of [[413, "too_large"], [422, "invalid"], [429, "ip_limit"], [429, "daily_limit"], [429, "personal_limit"]] as const) {
    assert.deepEqual(Object.keys(uploadProblems(http(status, code))), ["form"], code);
  }
});

test("every limit has its own words", () => {
  const texts = ["ip_limit", "daily_limit", "personal_limit"].map((code) => uploadProblems(http(429, code)).form);
  assert.equal(new Set(texts).size, 3);
  assert.equal(orderErrorText(http(429, "personal_limit")), uploadProblems(http(429, "personal_limit")).form);
});

test("a network failure and an unknown answer both promise the form is kept", () => {
  assert.match(uploadProblems(new TypeError("Failed to fetch")).form!, /Нет связи.*на месте/);
  assert.match(uploadProblems(http(500, null)).form!, /на нашей стороне.*на месте/);
  assert.match(uploadProblems(http(418, "whatever")).form!, /на нашей стороне/);
});

test("the service's own account problems are not the visitor's to fix", () => {
  assert.match(jobErrorText("no_credits"), /временно недоступен/);
  assert.match(jobErrorText("provider_auth"), /временно недоступен/);
  for (const code of ["provider_rejected", "provider_failed", "submit_unknown", "deadline", "download_failed", "internal", null]) {
    assert.match(jobErrorText(code), /не получился/, String(code));
  }
});

test("a rejection names the right photo", () => {
  assert.match(rejectText("no_face_1").title, new RegExp(PHOTO_LABEL.photo1));
  assert.match(rejectText("no_face_2").title, new RegExp(PHOTO_LABEL.photo2));
  assert.notEqual(PHOTO_LABEL.photo1, PHOTO_LABEL.photo2);
});

test("each rejection reason has its own explanation", () => {
  const reasons = ["no_face_1", "no_face_2", "minor", "unsafe_photo", "famous_person", "photo_text", "unsafe_wish", "bad_wish", null];
  assert.equal(new Set(reasons.map((r) => rejectText(r).title)).size, reasons.length);
});

test("a rejection marks only what is wrong on the form", () => {
  assert.deepEqual(Object.keys(rejectProblems("no_face_1")), ["photo1"]);
  assert.deepEqual(Object.keys(rejectProblems("no_face_2")), ["photo2"]);
  assert.deepEqual(Object.keys(rejectProblems("unsafe_wish")), ["wish"]);
  assert.deepEqual(Object.keys(rejectProblems("bad_wish")), ["wish"]);
  assert.deepEqual(Object.keys(rejectProblems("minor")), ["form"]);
  assert.deepEqual(Object.keys(rejectProblems("unsafe_photo")), ["form"]);
});

test("a photo is checked before it is sent", () => {
  assert.equal(checkFile(sized("a.jpg", "image/jpeg", 1000)), null);
  assert.equal(checkFile(sized("a.webp", "image/webp", MAX_PHOTO)), null);
  assert.equal(checkFile(sized("IMG_1.HEIC", "", 1000)), null);
  assert.match(checkFile(sized("a.jpg", "image/jpeg", MAX_PHOTO + 1))!, /25 МБ/);
  assert.match(checkFile(sized("a.pdf", "application/pdf", 1000))!, /не подойдёт/);
  assert.match(checkFile(sized("a.gif", "image/gif", 1000))!, /не подойдёт/);
});

test("deleting a busy shoot explains itself", () => {
  assert.match(deleteErrorText(http(409, "busy")), /ещё создаётся/);
  assert.match(deleteErrorText(new TypeError("x")), /Не получилось/);
});

test("registering checks the rules before asking the server", () => {
  assert.equal(registerError("Анна", "123456"), null);
  assert.equal(registerError("Ёж1", "x".repeat(128)), null);
  assert.match(registerError("ab", "123456")!, /нике/);
  assert.match(registerError("a".repeat(21), "123456")!, /нике/);
  assert.match(registerError("с пробелом", "123456")!, /нике/);
  assert.match(registerError("Анна", "12345")!, /пароле/);
  assert.match(registerError("Анна", "x".repeat(129))!, /пароле/);
});

test("each login answer has its own words", () => {
  const texts = ["bad_credentials", "nickname_taken", "locked", "invalid"].map((code) => loginErrorText(http(400, code)));
  assert.equal(new Set(texts).size, 4);
  assert.match(loginErrorText(new TypeError("x")), /Нет связи/);
  assert.match(loginErrorText(http(500, null)), /на нашей стороне/);
});

test("an upload sends the form as it is and reads the error code", async (t) => {
  const seen: RequestInit[] = [];
  t.mock.method(globalThis, "fetch", async (_: string, init: RequestInit) => {
    seen.push(init);
    return new Response(JSON.stringify({ detail: "bad_image_2" }), { status: 422 });
  });
  const form = new FormData();
  await assert.rejects(createShoot(form), (e: unknown) => e instanceof HttpError && e.detail === "bad_image_2");
  assert.equal(seen[0].body, form);
  assert.equal((seen[0].headers as undefined), undefined);
});
