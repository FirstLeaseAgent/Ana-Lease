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
  role text NOT NULL CHECK (role IN ('solicitante','contacto','aval','representante','accionista')),
  subject_type text NOT NULL CHECK (subject_type IN ('PF','PM')),
  rfc text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, intake_id)
);
ALTER TABLE participants DROP CONSTRAINT IF EXISTS participants_role_check;
ALTER TABLE participants ADD CONSTRAINT participants_role_check
  CHECK (role IN ('solicitante','contacto','aval','representante','accionista'));
ALTER TABLE participants ADD COLUMN IF NOT EXISTS company_id uuid;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='participants' AND column_name='subject_type_confirmed') THEN
    ALTER TABLE participants ADD COLUMN subject_type_confirmed boolean NOT NULL DEFAULT true;
    UPDATE participants SET subject_type_confirmed=false WHERE role IN ('aval','accionista') AND rfc IS NULL;
  END IF;
END $$;
ALTER TABLE participants DROP CONSTRAINT IF EXISTS participants_company_fkey;
ALTER TABLE participants ADD CONSTRAINT participants_company_fkey
  FOREIGN KEY (company_id,intake_id) REFERENCES participants(id,intake_id);
ALTER TABLE participants DROP CONSTRAINT IF EXISTS participants_shareholder_company_check;
ALTER TABLE participants ADD CONSTRAINT participants_shareholder_company_check
  CHECK ((role='accionista' AND company_id IS NOT NULL) OR (role<>'accionista' AND company_id IS NULL));
CREATE UNIQUE INDEX IF NOT EXISTS shareholder_company_rfc ON participants(intake_id,company_id,rfc) WHERE role='accionista' AND rfc IS NOT NULL;
ALTER TABLE intakes ADD COLUMN IF NOT EXISTS shareholders_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE intakes ADD COLUMN IF NOT EXISTS shareholders_complete boolean NOT NULL DEFAULT false;
ALTER TABLE intakes ADD COLUMN IF NOT EXISTS guarantors_complete boolean NOT NULL DEFAULT false;
ALTER TABLE intakes ADD COLUMN IF NOT EXISTS capture_version bigint NOT NULL DEFAULT 0;
ALTER TABLE intakes ADD COLUMN IF NOT EXISTS capture_active_id uuid;
CREATE UNIQUE INDEX IF NOT EXISTS one_applicant ON participants(intake_id) WHERE role='solicitante';
CREATE UNIQUE INDEX IF NOT EXISTS one_contact ON participants(intake_id) WHERE role='contacto';
CREATE TABLE IF NOT EXISTS answers (
  intake_id uuid NOT NULL REFERENCES intakes(id) ON DELETE CASCADE,
  participant_id uuid NOT NULL,
  field_code text NOT NULL,
  value_json jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (participant_id, field_code),
  FOREIGN KEY (participant_id, intake_id) REFERENCES participants(id, intake_id)
);
CREATE TABLE IF NOT EXISTS capture_turns (
  intake_id uuid NOT NULL REFERENCES intakes(id) ON DELETE CASCADE,
  request_id uuid NOT NULL,
  user_message text NOT NULL,
  status text NOT NULL CHECK (status IN ('processing','complete','failed')),
  attempts integer NOT NULL DEFAULT 1,
  response_json jsonb,
  audit_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (intake_id,request_id)
);
CREATE INDEX IF NOT EXISTS capture_turns_created ON capture_turns(created_at);

CREATE TABLE IF NOT EXISTS document_dependencies (
  intake_id uuid NOT NULL,
  participant_id uuid NOT NULL,
  field_code text NOT NULL,
  value text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(participant_id,field_code),
  FOREIGN KEY(participant_id,intake_id) REFERENCES participants(id,intake_id)
);
CREATE TABLE IF NOT EXISTS document_uploads (
  id uuid PRIMARY KEY,
  intake_id uuid NOT NULL REFERENCES intakes(id),
  participant_id uuid NOT NULL,
  document_code text NOT NULL,
  sha256 text NOT NULL,
  byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 10485760),
  mime_type text NOT NULL,
  status text NOT NULL CHECK(status IN ('processing','received','failed')),
  storage_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id,intake_id),
  FOREIGN KEY(participant_id,intake_id) REFERENCES participants(id,intake_id)
);
CREATE TABLE IF NOT EXISTS document_states (
  intake_id uuid NOT NULL,
  participant_id uuid NOT NULL,
  document_code text NOT NULL,
  status text NOT NULL CHECK(status IN ('received','deferred')),
  upload_id uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(participant_id,document_code),
  FOREIGN KEY(participant_id,intake_id) REFERENCES participants(id,intake_id),
  FOREIGN KEY(upload_id,intake_id) REFERENCES document_uploads(id,intake_id)
);
CREATE TABLE IF NOT EXISTS document_events (
  id bigserial PRIMARY KEY,
  intake_id uuid NOT NULL REFERENCES intakes(id),
  actor_id uuid NOT NULL REFERENCES users(id),
  participant_id uuid NOT NULL,
  document_code text,
  action text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(participant_id,intake_id) REFERENCES participants(id,intake_id)
);
CREATE INDEX IF NOT EXISTS document_dependencies_intake ON document_dependencies(intake_id);
CREATE INDEX IF NOT EXISTS document_states_intake ON document_states(intake_id);
CREATE INDEX IF NOT EXISTS document_uploads_intake_created ON document_uploads(intake_id,created_at);
CREATE INDEX IF NOT EXISTS document_events_intake_created ON document_events(intake_id,created_at);

ALTER TABLE intakes ADD COLUMN IF NOT EXISTS catalog_snapshot jsonb;
CREATE TABLE IF NOT EXISTS capture_catalogs (
  id bigserial PRIMARY KEY,
  actor_id uuid NOT NULL REFERENCES users(id),
  body jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS submission_notifications (
  intake_id uuid PRIMARY KEY REFERENCES intakes(id),
  required_missing integer NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed')),
  attempts integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contact_requests (
  id uuid PRIMARY KEY,
  name text NOT NULL,
  phone text NOT NULL,
  phone_hash text NOT NULL,
  ip_hash text NOT NULL,
  owner_id uuid REFERENCES users(id),
  intake_id uuid REFERENCES intakes(id),
  notification_status text NOT NULL DEFAULT 'pending' CHECK (notification_status IN ('pending','sending','sent','failed')),
  attempts integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contact_phone_created ON contact_requests(phone_hash,created_at DESC);
CREATE INDEX IF NOT EXISTS contact_ip_created ON contact_requests(ip_hash,created_at DESC);

CREATE TABLE IF NOT EXISTS capture_pending_fields (
  intake_id uuid NOT NULL,
  participant_id uuid NOT NULL,
  field_code text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(participant_id,field_code),
  FOREIGN KEY(participant_id,intake_id) REFERENCES participants(id,intake_id)
);
CREATE INDEX IF NOT EXISTS pending_fields_intake ON capture_pending_fields(intake_id);
