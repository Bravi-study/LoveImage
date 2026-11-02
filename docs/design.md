# LoveImage: design

A visitor uploads two photos (a guy and a girl) and an optional wish about style or look. The site returns their joint
romantic photo. A simple personal cabinet keeps the history. Payments are not built yet.

## What happens to a shoot

```
POST /api/shoots -> shoot "uploaded" -> worker asks the language model about both photos and the wish
  rejected -> reject_reason is set, the photos are deleted
  failed   -> the check could not run (provider down until the 90 s deadline): POST .../analysis repeats it
  ready    -> the first job is ordered in the same transaction
                queued -> submitting -> submitted -> succeeded | failed
```

`ready` with no jobs means a daily limit was reached at that moment: the page offers a button doing `POST .../jobs`.
A job in `failed` with `can_retry` repeats with `{"variant": N}`. `{}` orders a new variant. One running job per shoot.

Only one fixed template reaches the image model, with a `scene_en` written by the language model and passed through an
allowlist. The visitor's own words never reach it. An empty wish draws a random preset scene.

Why a failed paid call is never repeated by itself: kie.ai has no idempotency key, so the worker commits `submitting`
before `createTask`, and an unknown outcome becomes `failed(submit_unknown)`.

## API

All JSON unless noted. A session cookie is set on the first upload, so a guest has a history with no sign-up. Errors are
`{"detail": "<code>"}`. An unexpected failure answers 500 with the errno name or the exception's class as the code, and the page shows it. A stranger's shoot answers 404, the same as a missing one.

| Request | Answer | Errors |
|---|---|---|
| `POST /api/shoots` multipart: `photo1`, `photo2`, `wish` (optional, up to 300), `consent=true` | 201 `Shoot` | 422 `invalid` (no consent or photo), `bad_wish`, `bad_image_1`, `bad_image_2`; 413 `too_large` (25 MB per photo); 429 `ip_limit`, `daily_limit`, `personal_limit` |
| `GET /api/shoots/{id}` | `Shoot` | 404 |
| `GET /api/shoots?before=<created_at>` | `ShootCard[]`, newest first, up to 30; rejected are left out. URL-encode `before`, it contains `+` | |
| `POST /api/shoots/{id}/jobs` `{}` or `{"variant": N}` | 202 `Shoot` | 409 `not_ready`, `not_retryable`; 429 as above |
| `POST /api/shoots/{id}/analysis` | `Shoot` | 409 `not_failed` |
| `DELETE /api/shoots/{id}` | 204 | 409 `busy` (a job is running) |
| `GET /api/shoots/{id}/photos/{1,2}`, `GET /api/shoots/{id}/results/{variant}` | JPEG, owner only | 404 |
| `POST /api/register`, `POST /api/login` `{"nickname", "password"}` | `{"nickname"}` | 422 `invalid`; 409 `nickname_taken`; 401 `bad_credentials`; 429 `locked` |
| `POST /api/logout` | 204 | |
| `GET /api/me` | `{"nickname": string or null}`, null for a guest | |

Nickname: 3 to 20 letters or digits, Latin or Cyrillic. Password: 6 to 128 characters. Registering turns the guest into the
account and keeps the shoots. Logging in moves the guest's shoots to the account.

```ts
Shoot = { id, status: "uploaded"|"ready"|"rejected"|"failed", reject_reason, wish, created_at,
          photo_urls: string[], jobs: Job[] }   // photo_urls is empty once rejected
Job = { variant, status: "queued"|"submitting"|"submitted"|"succeeded"|"failed", error, can_retry, result_url }
ShootCard = { id, status, wish, created_at, cover_url, images, active }
```

`reject_reason`: `minor`, `unsafe_photo`, `famous_person`, `photo_text`, `no_face_1`, `no_face_2`, `unsafe_wish`, `bad_wish`.
On a `failed` shoot the same field says why the check could not run: `deadline` (no usable answer for 90 s), `llm_401`, `llm_402`, `llm_403` (the provider refused the account), `internal` (the worker crashed). The page shows these and `job.error` as the error code.

`job.error`: `no_credits`, `provider_auth` (the site's own account, the visitor can only wait), `provider_rejected`,
`provider_failed`, `submit_unknown`, `deadline`, `download_failed`, `internal`.

Timing with the real providers: the check takes seconds, the picture usually 1 to 3 minutes, the deadline is 7 minutes.

## Look

Adult romantic editorial, not a children's toy and not a dating app: warm ivory, deep wine, blush. A serif display face with
Cyrillic, a clean sans for the text. Two photo cards joined into one picture as the central image of the page. Phone first.

## Deliberately not built

Payments, watermark, admin panel, Telegram alerts, monitoring, format picker, more than two photos, video.
