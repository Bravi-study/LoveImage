CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nickname text,
  nickname_key text,
  password_hash text,
  failed_logins int NOT NULL DEFAULT 0,
  locked_until timestamptz,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  -- A guest has none of the three, an account has all of them.
  CHECK ((nickname IS NULL) = (nickname_key IS NULL) AND (nickname IS NULL) = (password_hash IS NULL))
);
CREATE UNIQUE INDEX users_nickname_key_idx ON users (nickname_key);

CREATE TABLE sessions (
  token_hash bytea PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_id_idx ON sessions (user_id);

CREATE TABLE user_usage (
  user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  day date NOT NULL,
  generations int NOT NULL,
  PRIMARY KEY (user_id, day)
);

-- Spend is kept per day and not summed from jobs: deleting a shoot must not shrink past spend.
CREATE TABLE daily_usage (
  day date PRIMARY KEY,
  generations int NOT NULL,
  credits int NOT NULL DEFAULT 0
);

CREATE TABLE shoots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  status text NOT NULL CHECK (status IN ('uploaded', 'ready', 'rejected', 'failed')),
  wish text NOT NULL DEFAULT '',
  scene_en text,
  reject_reason text,
  consent_at timestamptz NOT NULL,
  run_at timestamptz,
  deadline_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((status = 'uploaded') = (run_at IS NOT NULL))
);
CREATE INDEX shoots_run_at_idx ON shoots (run_at) WHERE run_at IS NOT NULL;
CREATE INDEX shoots_user_id_created_at_idx ON shoots (user_id, created_at);

CREATE TABLE jobs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  shoot_id uuid NOT NULL REFERENCES shoots ON DELETE CASCADE,
  variant int NOT NULL,
  status text NOT NULL CHECK (status IN ('queued', 'submitting', 'submitted', 'succeeded', 'failed')),
  attempt int NOT NULL DEFAULT 1,
  model text NOT NULL,
  prompt text NOT NULL,
  provider_job_id text,
  credits int NOT NULL DEFAULT 0,
  result_path text,
  error text,
  run_at timestamptz,
  deadline_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (shoot_id, variant),
  CHECK ((status IN ('queued', 'submitting', 'submitted')) = (run_at IS NOT NULL))
);
CREATE INDEX jobs_run_at_idx ON jobs (run_at) WHERE run_at IS NOT NULL;
