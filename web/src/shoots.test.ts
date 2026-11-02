import assert from "node:assert/strict";
import test from "node:test";

import { shootsPath, type Job, type Shoot, type ShootCard } from "./api.ts";
import { dateLabel, hasMore, isBusy, mergeCards, picturesLabel, pickView, statusText } from "./shoots.ts";

const job = (variant: number, status: string): Job => ({ variant, status, error: null, can_retry: false, result_url: null });
const shoot = (status: string, jobs: Job[] = [], reject_reason: string | null = null): Shoot => ({ id: "a", status, reject_reason, wish: "", created_at: "2026-11-02T10:00:00+00:00", photo_urls: [], jobs });
const card = (id: string, images = 0): ShootCard => ({ id, status: "ready", wish: "", created_at: "2026-11-02T10:00:00+00:00", cover_url: null, images, active: false });

test("polling goes on only while something can still change", () => {
  assert.equal(isBusy(shoot("uploaded")), true);
  for (const status of ["queued", "submitting", "submitted"]) assert.equal(isBusy(shoot("ready", [job(1, status)])), true, status);
  for (const status of ["succeeded", "failed"]) assert.equal(isBusy(shoot("ready", [job(1, status)])), false, status);
  assert.equal(isBusy(shoot("ready", [job(1, "succeeded"), job(2, "queued")])), true);
  assert.equal(isBusy(shoot("ready")), false);
  assert.equal(isBusy(shoot("rejected")), false);
  assert.equal(isBusy(shoot("failed")), false);
});

test("the view follows the status of the shoot", () => {
  assert.deepEqual(pickView(shoot("uploaded"), null), { name: "checking" });
  assert.deepEqual(pickView(shoot("failed", [], "deadline"), null), { name: "check_failed", code: "deadline" });
  assert.deepEqual(pickView(shoot("rejected", [], "minor"), null), { name: "rejected", reason: "minor" });
  assert.deepEqual(pickView(shoot("ready"), null), { name: "no_jobs" });
});

test("only the very first picture gets the whole waiting screen", () => {
  assert.deepEqual(pickView(shoot("ready", [job(1, "submitted")]), null), { name: "first_picture" });
  assert.equal(pickView(shoot("ready", [job(1, "failed")]), null).name, "pictures");
  assert.equal(pickView(shoot("ready", [job(1, "succeeded"), job(2, "queued")]), null).name, "pictures");
});

test("the visitor's variant wins, otherwise the latest is shown", () => {
  const jobs = [job(1, "succeeded"), job(2, "failed"), job(3, "succeeded")];
  const variant = (selected: number | null) => {
    const view = pickView(shoot("ready", jobs), selected);
    return view.name === "pictures" ? view.job.variant : null;
  };
  assert.equal(variant(null), 3);
  assert.equal(variant(1), 1);
  assert.equal(variant(9), 3);
});

test("the live status says what changed", () => {
  assert.equal(statusText({ name: "pictures", job: job(2, "queued") }), "Создаём вариант 2.");
  assert.equal(statusText({ name: "pictures", job: job(2, "succeeded") }), "Вариант 2 готов.");
  assert.equal(statusText({ name: "pictures", job: job(2, "failed") }), "Вариант 2 не получился.");
});

test("the cursor survives the query string", () => {
  assert.equal(shootsPath(), "/api/shoots");
  const before = "2026-11-02T10:36:14.123456+03:00";
  const path = shootsPath(before);
  assert.equal(path, "/api/shoots?before=2026-11-02T10%3A36%3A14.123456%2B03%3A00");
  assert.equal(new URL(path, "http://x").searchParams.get("before"), before);
});

test("a full page may have more after it, a short one has not", () => {
  assert.equal(hasMore(Array.from({ length: 30 }, (_, i) => card(String(i)))), true);
  assert.equal(hasMore(Array.from({ length: 29 }, (_, i) => card(String(i)))), false);
  assert.equal(hasMore([]), false);
});

test("a refresh updates known cards, adds new ones on top and keeps older pages", () => {
  const merged = mergeCards([card("b"), card("c"), card("d")], [card("a"), card("b", 2)]);
  assert.deepEqual(merged.map((c) => c.id), ["a", "b", "c", "d"]);
  assert.equal(merged[1].images, 2);
});

test("picture counts decline in Russian", () => {
  const cases: [number, string][] = [[0, "0 снимков"], [1, "1 снимок"], [2, "2 снимка"], [4, "4 снимка"], [5, "5 снимков"], [11, "11 снимков"], [21, "21 снимок"], [22, "22 снимка"]];
  for (const [n, text] of cases) assert.equal(picturesLabel(n), text);
});

test("a date from another year names the year", () => {
  const now = new Date("2026-11-02T12:00:00");
  assert.equal(dateLabel("2026-03-08T12:00:00", now).includes("2026"), false);
  assert.equal(dateLabel("2025-03-08T12:00:00", now).includes("2025"), true);
});
