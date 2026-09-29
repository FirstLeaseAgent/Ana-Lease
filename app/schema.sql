CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS otp_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL,
  ip_hash text NOT NULL,
  code_hash text NOT NULL,
  attempts smallint NOT NULL DEFAULT 0,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS otp_email_created ON otp_challenges(email, created_at DESC);
CREATE INDEX IF NOT EXISTS otp_ip_created ON otp_challenges(ip_hash, created_at DESC);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS intakes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES users(id),
  rfc text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','submitted')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, rfc)
);
CREATE INDEX IF NOT EXISTS intakes_owner ON intakes(owner_id);
CREATE TABLE IF NOT EXISTS participants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  intake_id uuid NOT NULL REFERENCES intakes(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('solicitante','aval','representante')),
  subject_type text NOT NULL CHECK (subject_type IN ('PF','PM')),
  rfc text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, intake_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_applicant ON participants(intake_id) WHERE role='solicitante';
CREATE TABLE IF NOT EXISTS answers (
  intake_id uuid NOT NULL REFERENCES intakes(id) ON DELETE CASCADE,
  participant_id uuid NOT NULL,
  field_code text NOT NULL,
  value_json jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (participant_id, field_code),
  FOREIGN KEY (participant_id, intake_id) REFERENCES participants(id, intake_id)
);
