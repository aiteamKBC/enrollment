CREATE SCHEMA IF NOT EXISTS "Coach";
CREATE SCHEMA IF NOT EXISTS "Learner";
CREATE SCHEMA IF NOT EXISTS "curriculum";
CREATE SCHEMA IF NOT EXISTS "enrolment";
CREATE SCHEMA IF NOT EXISTS "login";
SET LOCAL check_function_bodies = false;
CREATE OR REPLACE FUNCTION "Learner".set_learners_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$function$
;
CREATE OR REPLACE FUNCTION "Learner".sync_review_lifecycle_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.lifecycle_status IS DISTINCT FROM OLD.lifecycle_status THEN
        UPDATE "Learner".reviews
        SET learner_lifecycle_status = NEW.lifecycle_status
        WHERE learner_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$function$
;
CREATE OR REPLACE FUNCTION "Coach".aptem_actual_session_claim_valid(doc jsonb, overlay_event_key text, overlay_profile_id integer, overlay_source_id bigint)
 RETURNS boolean
 LANGUAGE plpgsql
 IMMUTABLE
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
    c jsonb;
    k text;
BEGIN
    -- Legacy rows without a claim are outside this new constraint.
    IF jsonb_typeof(doc) IS DISTINCT FROM 'object' THEN RETURN true; END IF;
    IF NOT (doc ? 'actualSession') THEN RETURN true; END IF;
    c := doc -> 'actualSession';
    IF jsonb_typeof(c) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    FOR k IN SELECT jsonb_object_keys(c) LOOP
        IF k <> ALL (ARRAY[
            'schemaVersion','selectionVersion','sourceReviewId','profileId',
            'calendarId','eventKey','overlayEventKey','ownerEmail','graphEventId',
            'bookingFingerprint','tenantId','organizerId','onlineMeetingId',
            'callId','reportId','associationMethod','claimedAt','actorAccountId'
        ]) THEN RETURN false; END IF;
    END LOOP;
    IF c -> 'schemaVersion' IS DISTINCT FROM '1'::jsonb
       OR c -> 'selectionVersion' IS DISTINCT FROM '1'::jsonb
       OR jsonb_typeof(c -> 'sourceReviewId') IS DISTINCT FROM 'number'
       OR (c ->> 'sourceReviewId') !~ '^[1-9][0-9]{0,18}$'
       OR c ->> 'sourceReviewId' IS DISTINCT FROM overlay_source_id::text
       OR jsonb_typeof(c -> 'profileId') IS DISTINCT FROM 'number'
       OR (c ->> 'profileId') !~ '^[1-9][0-9]{0,18}$'
       OR c ->> 'profileId' IS DISTINCT FROM overlay_profile_id::text
       OR jsonb_typeof(c -> 'calendarId') IS DISTINCT FROM 'number'
       OR (c ->> 'calendarId') !~ '^[1-9][0-9]{0,18}$'
       OR c ->> 'overlayEventKey' IS DISTINCT FROM overlay_event_key
       OR jsonb_typeof(c -> 'actorAccountId') IS DISTINCT FROM 'number'
       OR (c ->> 'actorAccountId') !~ '^[1-9][0-9]{0,18}$'
       OR c ->> 'associationMethod' IS DISTINCT FROM 'single-run-corroborated'
       OR jsonb_typeof(c -> 'claimedAt') IS DISTINCT FROM 'string'
       OR (c ->> 'claimedAt') !~ '^20[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?Z$'
       OR jsonb_typeof(c -> 'bookingFingerprint') IS DISTINCT FROM 'string'
       OR (c ->> 'bookingFingerprint') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(c -> 'tenantId') IS DISTINCT FROM 'string'
       OR (c ->> 'tenantId') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR c ->> 'tenantId' = '00000000-0000-0000-0000-000000000000'
       OR jsonb_typeof(c -> 'organizerId') IS DISTINCT FROM 'string'
       OR (c ->> 'organizerId') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR c ->> 'organizerId' = '00000000-0000-0000-0000-000000000000'
    THEN RETURN false; END IF;
    BEGIN
        PERFORM make_date(substring(c ->> 'claimedAt' from 1 for 4)::integer,
                          substring(c ->> 'claimedAt' from 6 for 2)::integer,
                          substring(c ->> 'claimedAt' from 9 for 2)::integer);
    EXCEPTION WHEN datetime_field_overflow THEN
        RETURN false;
    END;
    FOREACH k IN ARRAY ARRAY[
        'eventKey','overlayEventKey','ownerEmail','graphEventId','onlineMeetingId','callId','reportId'
    ] LOOP
        IF jsonb_typeof(c -> k) IS DISTINCT FROM 'string'
           OR octet_length(c ->> k) NOT BETWEEN 1 AND 240
           OR (c ->> k) !~ '^[!-~]+$'
           OR (c ->> k) LIKE '%://%'
        THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
END
$function$
;
CREATE OR REPLACE FUNCTION "Coach".aptem_actual_session_claim_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
    prior jsonb;
    incoming jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.meeting_intelligence ? 'actualSession' THEN
            RAISE EXCEPTION 'An Actual Session claim reserves historical provider identity.'
                USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    incoming := NEW.meeting_intelligence -> 'actualSession';
    IF TG_OP = 'INSERT' THEN
        IF incoming IS NOT NULL THEN
            RAISE EXCEPTION 'An Actual Session claim requires an existing Review.'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    prior := OLD.meeting_intelligence -> 'actualSession';
    IF prior IS NOT NULL THEN
        IF incoming IS DISTINCT FROM prior
           OR NEW.source_review_id IS DISTINCT FROM OLD.source_review_id
           OR NEW.learner_id IS DISTINCT FROM OLD.learner_id
           OR NEW.event_key IS DISTINCT FROM OLD.event_key THEN
            RAISE EXCEPTION 'An Actual Session claim cannot be changed or transferred.'
                USING ERRCODE = '23514';
        END IF;
    ELSIF incoming IS NOT NULL THEN
        IF NEW.status NOT IN ('scheduled', 'in-progress')
           OR NEW.owner_email IS DISTINCT FROM incoming ->> 'ownerEmail' THEN
            RAISE EXCEPTION 'An Actual Session claim requires an owned editable Review.'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$function$
;
CREATE OR REPLACE FUNCTION "Coach".aptem_imported_meeting_owner_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Verified meeting ownership cannot be deleted.' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF NEW.source_review_id IS DISTINCT FROM OLD.source_review_id
      OR NEW.learner_id IS DISTINCT FROM OLD.learner_id
      OR NEW.review_event_key IS DISTINCT FROM OLD.review_event_key
      OR NEW.calendar_event_id IS DISTINCT FROM OLD.calendar_event_id
      OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
      OR NEW.organizer_id IS DISTINCT FROM OLD.organizer_id
      OR NEW.online_meeting_id IS DISTINCT FROM OLD.online_meeting_id
      OR NEW.graph_event_id IS DISTINCT FROM OLD.graph_event_id
      OR NEW.provenance IS DISTINCT FROM OLD.provenance
      OR NEW.verified_at IS DISTINCT FROM OLD.verified_at
      OR (OLD.state = 'cancelled' AND NEW.state <> 'cancelled') THEN
      RAISE EXCEPTION 'Verified meeting ownership cannot be transferred or replaced.'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  IF NOT EXISTS (
      SELECT 1 FROM "Learner".reviews s
      WHERE s.id = NEW.source_review_id AND s.learner_id = NEW.learner_id
        AND 'imported-review:' || s.aptem_review_id::text = NEW.review_event_key
    ) OR NOT EXISTS (
      SELECT 1 FROM "Learner".coach_calendar_event c
      WHERE c.id = NEW.calendar_event_id AND c.learner_id = NEW.learner_id
        AND c.event_key = NEW.review_event_key
        AND lower(c.owner_email) = lower(NEW.owner_email)
        AND c.graph_event_id = NEW.graph_event_id
    ) OR (NEW.overlay_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM "Coach".coach_imported_review_instance o
      WHERE o.id = NEW.overlay_id AND o.source_review_id = NEW.source_review_id
        AND o.learner_id = NEW.learner_id AND o.event_key = NEW.review_event_key
        AND lower(o.owner_email) = lower(NEW.owner_email)
    )) THEN
    RAISE EXCEPTION 'Verified meeting ownership association is inconsistent.'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $function$
;
CREATE OR REPLACE FUNCTION "Coach".aptem_owned_calendar_identity_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
  IF EXISTS (SELECT 1 FROM "Coach".coach_imported_meeting_ownership o
             WHERE o.calendar_event_id = OLD.id AND o.state = 'active') THEN
    IF TG_OP = 'DELETE' THEN
      RAISE EXCEPTION 'Owned imported meeting calendar cannot be deleted.'
        USING ERRCODE = '23514';
    END IF;
    IF (NEW.event_key, NEW.learner_id, NEW.owner_email, NEW.graph_event_id,
        NEW.graph_organizer_email, NEW.meeting_link, NEW.graph_web_link)
       IS DISTINCT FROM
       (OLD.event_key, OLD.learner_id, OLD.owner_email, OLD.graph_event_id,
        OLD.graph_organizer_email, OLD.meeting_link, OLD.graph_web_link)
       AND current_setting('coach.aptem_owner_verified_sync', true) IS DISTINCT FROM 'on'
    THEN
      RAISE EXCEPTION 'Owned imported meeting identity requires verified synchronization.'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $function$
;
--
-- PostgreSQL database dump
--


-- Dumped from database version 18.6 (fd34222)
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: coach_calendar_color_preference; Type: TABLE; Schema: Coach; Owner: -
--

CREATE TABLE "Coach".coach_calendar_color_preference (
    id bigint NOT NULL,
    owner_email character varying(255) NOT NULL,
    scope character varying(16) NOT NULL,
    scope_key character varying(255) NOT NULL,
    color character varying(7) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT coach_color_pref_color_format_valid CHECK (((color)::text ~ '^#[0-9A-Fa-f]{6}$'::text)),
    CONSTRAINT coach_color_pref_scope_valid CHECK (((scope)::text = ANY ((ARRAY['category'::character varying, 'event'::character varying])::text[])))
);


--
-- Name: coach_calendar_color_preference_id_seq; Type: SEQUENCE; Schema: Coach; Owner: -
--

ALTER TABLE "Coach".coach_calendar_color_preference ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Coach".coach_calendar_color_preference_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: coach_calendar_event; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".coach_calendar_event (
    id bigint CONSTRAINT coach_api_coachcalendarevent_id_not_null NOT NULL,
    event_key character varying(255) CONSTRAINT coach_api_coachcalendarevent_event_key_not_null NOT NULL,
    owner_email character varying(255) CONSTRAINT coach_api_coachcalendarevent_owner_email_not_null NOT NULL,
    owner_name character varying(255) CONSTRAINT coach_api_coachcalendarevent_owner_name_not_null NOT NULL,
    learner_id integer CONSTRAINT coach_api_coachcalendarevent_learner_id_not_null NOT NULL,
    learner_name character varying(255) CONSTRAINT coach_api_coachcalendarevent_learner_name_not_null NOT NULL,
    learner_email character varying(255) CONSTRAINT coach_api_coachcalendarevent_learner_email_not_null NOT NULL,
    event_type character varying(32) CONSTRAINT coach_api_coachcalendarevent_event_type_not_null NOT NULL,
    sequence integer CONSTRAINT coach_api_coachcalendarevent_sequence_not_null NOT NULL,
    target_date date CONSTRAINT coach_api_coachcalendarevent_target_date_not_null NOT NULL,
    scheduled_date date,
    scheduled_time time without time zone,
    duration_minutes integer CONSTRAINT coach_api_coachcalendarevent_duration_minutes_not_null NOT NULL,
    status character varying(32) CONSTRAINT coach_api_coachcalendarevent_status_not_null NOT NULL,
    meeting_provider character varying(64) CONSTRAINT coach_api_coachcalendarevent_meeting_provider_not_null NOT NULL,
    meeting_link character varying(1000) CONSTRAINT coach_api_coachcalendarevent_meeting_link_not_null NOT NULL,
    graph_event_id character varying(255) CONSTRAINT coach_api_coachcalendarevent_graph_event_id_not_null NOT NULL,
    graph_web_link character varying(1000) CONSTRAINT coach_api_coachcalendarevent_graph_web_link_not_null NOT NULL,
    notes text CONSTRAINT coach_api_coachcalendarevent_notes_not_null NOT NULL,
    last_graph_sync_error text CONSTRAINT coach_api_coachcalendarevent_last_graph_sync_error_not_null NOT NULL,
    created_at timestamp with time zone CONSTRAINT coach_api_coachcalendarevent_created_at_not_null NOT NULL,
    updated_at timestamp with time zone CONSTRAINT coach_api_coachcalendarevent_updated_at_not_null NOT NULL,
    review_responses jsonb NOT NULL,
    review_completed_at timestamp with time zone,
    manager_signed_at timestamp with time zone,
    manager_signed_by character varying(255) NOT NULL,
    graph_organizer_email character varying(255) DEFAULT ''::character varying NOT NULL,
    operation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    idempotency_key character varying(255) NOT NULL,
    sync_state character varying(24) NOT NULL,
    sync_attempt_count integer NOT NULL,
    last_sync_attempt_at timestamp with time zone,
    review_template_id character varying(128),
    review_instance_id character varying(128),
    occurrence_number integer,
    CONSTRAINT coach_api_coachcalendarevent_duration_minutes_check CHECK ((duration_minutes >= 0)),
    CONSTRAINT coach_api_coachcalendarevent_sequence_check CHECK ((sequence >= 0)),
    CONSTRAINT coach_calendar_event_status_valid CHECK (((status)::text = ANY ((ARRAY['not-scheduled'::character varying, 'scheduled'::character varying, 'in-progress'::character varying, 'awaiting-signature'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT coach_calendar_sync_attempt_nonnegative CHECK ((sync_attempt_count >= 0)),
    CONSTRAINT coach_calendar_sync_state_valid CHECK (((sync_state)::text = ANY ((ARRAY['pending'::character varying, 'syncing'::character varying, 'synced'::character varying, 'failed'::character varying, 'reconciliation'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: coach_calendar_event; Type: VIEW; Schema: Coach; Owner: -
--

CREATE VIEW "Coach".coach_calendar_event AS
 SELECT id,
    event_key,
    owner_email,
    owner_name,
    learner_id,
    learner_name,
    learner_email,
    event_type,
    sequence,
    target_date,
    scheduled_date,
    scheduled_time,
    duration_minutes,
    status,
    meeting_provider,
    meeting_link,
    graph_event_id,
    graph_web_link,
    notes,
    last_graph_sync_error,
    created_at,
    updated_at,
    review_responses,
    review_completed_at,
    manager_signed_at,
    manager_signed_by,
    graph_organizer_email,
    operation_id,
    idempotency_key,
    sync_state,
    sync_attempt_count,
    last_sync_attempt_at,
    review_template_id,
    review_instance_id,
    occurrence_number
   FROM "Learner".coach_calendar_event;


--
-- Name: coach_calendar_sequence; Type: TABLE; Schema: Coach; Owner: -
--

CREATE TABLE "Coach".coach_calendar_sequence (
    id bigint NOT NULL,
    learner_id integer NOT NULL,
    event_type character varying(32) NOT NULL,
    last_sequence integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT coach_calendar_sequence_last_nonnegative CHECK ((last_sequence >= 0))
);


--
-- Name: coach_calendar_sequence_id_seq; Type: SEQUENCE; Schema: Coach; Owner: -
--

ALTER TABLE "Coach".coach_calendar_sequence ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Coach".coach_calendar_sequence_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: coach_imported_meeting_ownership; Type: TABLE; Schema: Coach; Owner: -
--

CREATE TABLE "Coach".coach_imported_meeting_ownership (
    id bigint NOT NULL,
    source_review_id bigint NOT NULL,
    learner_id integer NOT NULL,
    review_event_key character varying(255) NOT NULL,
    calendar_event_id bigint NOT NULL,
    owner_email character varying(255) NOT NULL,
    tenant_id uuid NOT NULL,
    organizer_id uuid NOT NULL,
    online_meeting_id character varying(240) NOT NULL,
    graph_event_id character varying(255) NOT NULL,
    booking_fingerprint character varying(64) NOT NULL,
    provenance character varying(32) NOT NULL,
    state character varying(16) NOT NULL,
    verified_at timestamp with time zone NOT NULL,
    overlay_id bigint,
    CONSTRAINT aptem_owner_meeting_nonempty CHECK ((NOT ((online_meeting_id)::text = ''::text))),
    CONSTRAINT aptem_owner_provider_valid CHECK (((tenant_id <> '00000000-0000-0000-0000-000000000000'::uuid) AND (organizer_id <> '00000000-0000-0000-0000-000000000000'::uuid) AND ((online_meeting_id)::text ~ '^[!-~]+$'::text) AND ((online_meeting_id)::text !~~ '%://%'::text) AND ((graph_event_id)::text <> ''::text) AND ((booking_fingerprint)::text ~ '^[0-9a-f]{64}$'::text) AND ((provenance)::text = 'graph-calendar-verified'::text))),
    CONSTRAINT aptem_owner_state_valid CHECK (((state)::text = ANY ((ARRAY['active'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: coach_imported_meeting_ownership_id_seq; Type: SEQUENCE; Schema: Coach; Owner: -
--

ALTER TABLE "Coach".coach_imported_meeting_ownership ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Coach".coach_imported_meeting_ownership_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: coach_imported_review_instance; Type: TABLE; Schema: Coach; Owner: -
--

CREATE TABLE "Coach".coach_imported_review_instance (
    id bigint NOT NULL,
    event_key character varying(255) NOT NULL,
    owner_email character varying(255) NOT NULL,
    learner_id integer NOT NULL,
    answers jsonb NOT NULL,
    status character varying(32) NOT NULL,
    completed_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    source_review_id bigint,
    migrated_template_id bigint,
    template_snapshot jsonb NOT NULL,
    signature_requirements jsonb NOT NULL,
    meeting_intelligence jsonb NOT NULL,
    progress_snapshot jsonb,
    CONSTRAINT aptem_actual_session_claim_valid CHECK ("Coach".aptem_actual_session_claim_valid(meeting_intelligence, (event_key)::text, learner_id, source_review_id)),
    CONSTRAINT coach_imported_review_status_valid CHECK (((status)::text = ANY ((ARRAY['not-scheduled'::character varying, 'scheduled'::character varying, 'in-progress'::character varying, 'awaiting-signature'::character varying, 'completed'::character varying])::text[])))
);


--
-- Name: coach_imported_review_instance_id_seq; Type: SEQUENCE; Schema: Coach; Owner: -
--

ALTER TABLE "Coach".coach_imported_review_instance ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Coach".coach_imported_review_instance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: coach_migrated_review_template; Type: TABLE; Schema: Coach; Owner: -
--

CREATE TABLE "Coach".coach_migrated_review_template (
    id bigint NOT NULL,
    programme_key character varying(255) NOT NULL,
    review_family character varying(15) NOT NULL,
    name character varying(255) NOT NULL,
    definition_json jsonb NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    scope character varying(9) NOT NULL,
    source_metadata jsonb NOT NULL,
    expected_otjh numeric(8,2) DEFAULT 0 NOT NULL,
    counts_towards_otjh boolean DEFAULT false NOT NULL,
    CONSTRAINT coach_migrated_otjh_nonnegative CHECK ((expected_otjh >= (0)::numeric)),
    CONSTRAINT coach_migrated_template_family_valid CHECK (((review_family)::text = ANY ((ARRAY['MCM'::character varying, 'PR'::character varying, 'PR_SKILLS_RADAR'::character varying, 'PSP'::character varying])::text[]))),
    CONSTRAINT coach_migrated_template_scope_valid CHECK (((((programme_key)::text = ''::text) AND ((scope)::text = 'GLOBAL'::text)) OR (((scope)::text = 'PROGRAMME'::text) AND (NOT ((programme_key)::text = ''::text)))))
);


--
-- Name: coach_migrated_review_template_id_seq; Type: SEQUENCE; Schema: Coach; Owner: -
--

ALTER TABLE "Coach".coach_migrated_review_template ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Coach".coach_migrated_review_template_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: calendar_busy_slots; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".calendar_busy_slots (
    id bigint NOT NULL,
    connection_id bigint NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    busy_status text DEFAULT 'busy'::text NOT NULL,
    source_hash text NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT calendar_busy_valid_range CHECK ((ends_at > starts_at))
);


--
-- Name: calendar_busy_slots_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

CREATE SEQUENCE "Learner".calendar_busy_slots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: calendar_busy_slots_id_seq; Type: SEQUENCE OWNED BY; Schema: Learner; Owner: -
--

ALTER SEQUENCE "Learner".calendar_busy_slots_id_seq OWNED BY "Learner".calendar_busy_slots.id;


--
-- Name: calendar_connections; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".calendar_connections (
    id bigint NOT NULL,
    learner_kind text NOT NULL,
    learner_id bigint NOT NULL,
    provider text NOT NULL,
    account_email text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'connected'::text NOT NULL,
    credential_ciphertext text NOT NULL,
    calendar_url text DEFAULT ''::text NOT NULL,
    connected_at timestamp with time zone DEFAULT now() NOT NULL,
    last_sync_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    enrolment_id bigint
);


--
-- Name: calendar_connections_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

CREATE SEQUENCE "Learner".calendar_connections_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: calendar_connections_id_seq; Type: SEQUENCE OWNED BY; Schema: Learner; Owner: -
--

ALTER SEQUENCE "Learner".calendar_connections_id_seq OWNED BY "Learner".calendar_connections.id;


--
-- Name: coach_api_coachcalendarevent_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

ALTER TABLE "Learner".coach_calendar_event ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Learner".coach_api_coachcalendarevent_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: learner_free_courses; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".learner_free_courses (
    id bigint NOT NULL,
    learner_id bigint NOT NULL,
    "position" integer NOT NULL,
    free_course_ref text,
    free_course_title text DEFAULT ''::text NOT NULL
);


--
-- Name: learner_free_courses_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

ALTER TABLE "Learner".learner_free_courses ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Learner".learner_free_courses_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: learner_ksb_assignments; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".learner_ksb_assignments (
    learner_id bigint NOT NULL,
    profile_version_id bigint NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learner_ksbs; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".learner_ksbs (
    learner_id bigint NOT NULL,
    category text NOT NULL,
    code text NOT NULL,
    title text,
    level text
);


--
-- Name: learners; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".learners (
    id bigint NOT NULL,
    full_name text NOT NULL,
    email character varying(320) NOT NULL,
    email_normalized text GENERATED ALWAYS AS (lower(TRIM(BOTH FROM email))) STORED,
    phone_number text DEFAULT ''::text NOT NULL,
    lifecycle_status character varying(50) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    programme text DEFAULT ''::text NOT NULL,
    programme_status character varying(100) DEFAULT ''::character varying NOT NULL,
    cohort text DEFAULT ''::text NOT NULL,
    group_name text DEFAULT ''::text NOT NULL,
    completed_hours numeric(12,2),
    target_hours numeric(12,2),
    minimum_hours numeric(12,2),
    maximum_hours numeric(12,2),
    planned_hours numeric(12,2),
    progress_hours numeric(12,2),
    progress_variance numeric(10,4),
    otjh_status character varying(50) DEFAULT ''::character varying NOT NULL,
    coach_name text DEFAULT ''::text NOT NULL,
    coach_email character varying(320) DEFAULT ''::character varying NOT NULL,
    coach_rag character varying(20) DEFAULT ''::character varying NOT NULL,
    start_date date,
    end_date date,
    gateway_review_date date,
    alert_notify_for_epa date,
    enter_epa date,
    enrolment_id bigint,
    programme_id character varying(64),
    learner_type character varying(32),
    uuid uuid DEFAULT gen_random_uuid() NOT NULL,
    cohort_id character varying(64),
    group_id character varying(64),
    accepted_assignments integer DEFAULT 0 NOT NULL,
    rejected_assignments integer DEFAULT 0 NOT NULL,
    accepted_reflections integer DEFAULT 0 NOT NULL,
    rejected_reflections integer DEFAULT 0 NOT NULL,
    learning_plan jsonb,
    "Learner_Start_date" text,
    "Learner_end_date" text,
    free_courses jsonb DEFAULT '[]'::jsonb NOT NULL,
    aptem_id bigint,
    secondary_email character varying(320),
    CONSTRAINT learners_email_not_blank CHECK ((TRIM(BOTH FROM email) <> ''::text)),
    CONSTRAINT learners_learner_type_check CHECK (((learner_type IS NULL) OR (lower(btrim((learner_type)::text)) = ANY (ARRAY['apprenticeship'::text, 'commercial'::text])))),
    CONSTRAINT learners_status_not_blank CHECK ((TRIM(BOTH FROM lifecycle_status) <> ''::text))
);


--
-- Name: Created_users; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Created_users" (
    id integer NOT NULL,
    "Learner_type" text,
    "Invite_to_platform" boolean,
    "Username" text,
    "Preferred_name" text,
    "Title" text,
    "Gender" text,
    "Email" text,
    "Phone_number" text,
    "Date_of_birth" text,
    "National_insurance_number" text,
    "Referrer" text,
    "Referrer_address" text,
    "Referrer_contact" text,
    "Programme" text,
    "Cohort" text,
    "Group" text,
    "Current_postcode" text,
    "Current_address_line_1" text,
    "Current_address_line_2" text,
    "Current_address_line_3" text,
    "Current_address_line_4" text,
    "Country" text,
    "Case_owner" text,
    "Learning_provider" text,
    "Employer" text,
    "Employer_address" text,
    "Line_manager" text,
    "Mentor" text,
    "Orgnization" text,
    "Reference_number" text,
    "Extended_break" text,
    "Type" text,
    " Status" text,
    "Programme_status" text,
    "Start_date" text,
    "End_date" text,
    "Practical_period_end_date" text,
    "Apprenticeship_End_date" text,
    "Enrolled_time_and_user" text,
    "Onboarding_status" text,
    "Onboarding_completed" text,
    "Minimum_required_hours" text,
    "Planned_hours" text,
    "RPL_Hours" text,
    "Learning_plan" jsonb,
    "Training_plan" jsonb,
    "Modules" text,
    "Weeks" text,
    "Components" text,
    "Address" text,
    "Age" text,
    "Legal_Sex" text,
    "How_long_have_you_been_at_this_address_(years)?" text,
    "Postcode_prior_to_enrolment" text,
    "What_pronouns_do_you_use?" text,
    "Ethnicity" text,
    "Target_programme" text,
    "Allow_access_to_checkpoint" boolean,
    "Allow_access_to_console" boolean,
    "Allow_access_to_classic" boolean,
    "Sub-programme" json,
    "Aims/Qualifications" json,
    " English_Assessments" json,
    "Maths_Assessments" json,
    "ICT_Assessments" json,
    "English_Exemption_from_Functional_Skills" json,
    "Maths_Exemption_from_Functional_Skills" json,
    "ICT_Exemption_from_Functional_Skills" json,
    "Managed_jobs_and_placements/workshops" text,
    "Reviews" json,
    "Tracker" json,
    "Milestones" json,
    "Contacts" json,
    "Activity" json,
    " Compliance_documents" json,
    " Review_documents" json,
    "Documents" json,
    "Competencies" text,
    "Subscription_details" json,
    "Do_you_consider_yourself_to_have_a_long_term_disability_,_healt" json,
    "Contact_Preferences" json,
    "Emergency_contact_details " json,
    "Eligibility" json,
    "Other_training" text,
    "Personal_Circumstances" json,
    "Additional_information" json,
    "Media_Consent" text,
    "Declarations_/_consents" text,
    "Employer_id" integer,
    uuid uuid DEFAULT gen_random_uuid() NOT NULL,
    "Learner_signature" text,
    "Learner_signature_name" text,
    "Learner_signature_saved_at" timestamp with time zone,
    aptem_id text,
    "Coach_name" text,
    "Coach_email" text,
    "Learner_start_date" text,
    "Learner_end_date" text,
    "Free_courses" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "First_login_details_completed_at" timestamp with time zone,
    id_aptem text,
    employer_phone text,
    "Employer_email" text,
    "Attendance_type" text,
    learner_id bigint,
    CONSTRAINT created_users_attendance_type_check CHECK ((("Attendance_type" IS NULL) OR ("Attendance_type" = ANY (ARRAY['Recorded'::text, 'Not recorded'::text]))))
);


--
-- Name: learner_training_plan_components; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".learner_training_plan_components (
    id bigint NOT NULL,
    plan_week_id bigint NOT NULL,
    "position" integer NOT NULL,
    component_ref text,
    component_title text DEFAULT ''::text NOT NULL,
    curriculum_component_id character varying,
    component_type character varying(100) DEFAULT ''::character varying NOT NULL,
    expected_otjh numeric(12,2)
);


--
-- Name: learner_training_plan_components_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

ALTER TABLE "Learner".learner_training_plan_components ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Learner".learner_training_plan_components_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: learner_training_plan_modules; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".learner_training_plan_modules (
    id bigint NOT NULL,
    learner_id bigint NOT NULL,
    "position" integer NOT NULL,
    module_ref text,
    module_title text DEFAULT ''::text NOT NULL,
    curriculum_module_id character varying
);


--
-- Name: learner_training_plan_modules_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

ALTER TABLE "Learner".learner_training_plan_modules ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Learner".learner_training_plan_modules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: learner_training_plan_weeks; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".learner_training_plan_weeks (
    id bigint NOT NULL,
    plan_module_id bigint NOT NULL,
    "position" integer NOT NULL,
    week_ref text,
    week_title text DEFAULT ''::text NOT NULL,
    curriculum_week_id character varying
);


--
-- Name: learner_training_plan_weeks_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

ALTER TABLE "Learner".learner_training_plan_weeks ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Learner".learner_training_plan_weeks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: learners_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

ALTER TABLE "Learner".learners ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "Learner".learners_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: reviews; Type: TABLE; Schema: Learner; Owner: -
--

CREATE TABLE "Learner".reviews (
    id bigint NOT NULL,
    learner_id bigint NOT NULL,
    aptem_review_id text,
    review_name text,
    review_type text,
    reviewer_name text,
    planned_scheduled_date timestamp with time zone,
    completed_date timestamp with time zone,
    status text,
    source_url text,
    review_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    raw_text text,
    content_hash text,
    extraction_status text DEFAULT 'success'::text NOT NULL,
    last_error text,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_extracted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    learner_name text,
    learner_email text,
    source_review_program_id bigint,
    modern_listing_program_id bigint,
    modern_bucket text,
    visibility_state text DEFAULT 'unchecked'::text NOT NULL,
    visibility_checked_at timestamp with time zone,
    current_document_id bigint,
    current_document_created_at timestamp with time zone,
    current_document_program_id bigint,
    document_count integer,
    document_checked_at timestamp with time zone,
    learner_lifecycle_status character varying(50),
    CONSTRAINT reviews_extraction_status_check CHECK ((extraction_status = ANY (ARRAY['success'::text, 'partial'::text, 'failed'::text]))),
    CONSTRAINT reviews_modern_bucket_check CHECK (((modern_bucket IS NULL) OR (modern_bucket = ANY (ARRAY['planned'::text, 'finished'::text])))),
    CONSTRAINT reviews_visibility_state_check CHECK ((visibility_state = ANY (ARRAY['unchecked'::text, 'planned'::text, 'finished'::text, 'outside_modern_list'::text])))
);


--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: Learner; Owner: -
--

CREATE SEQUENCE "Learner".reviews_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: Learner; Owner: -
--

ALTER SEQUENCE "Learner".reviews_id_seq OWNED BY "Learner".reviews.id;


--
-- Name: activity_events; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.activity_events (
    id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_email text NOT NULL,
    actor_name text DEFAULT ''::text NOT NULL,
    actor_role text DEFAULT ''::text NOT NULL,
    account_id bigint,
    visit_id text DEFAULT ''::text NOT NULL,
    kind text NOT NULL,
    path text DEFAULT ''::text NOT NULL,
    page_key text DEFAULT ''::text NOT NULL,
    page_label text DEFAULT ''::text NOT NULL,
    target_type text DEFAULT ''::text NOT NULL,
    target_id text DEFAULT ''::text NOT NULL,
    target_label text DEFAULT ''::text NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    duration_ms integer,
    ip_address text,
    user_agent text,
    workspace text DEFAULT ''::text NOT NULL,
    CONSTRAINT activity_events_kind_check CHECK ((kind = ANY (ARRAY['page_view'::text, 'search'::text, 'filter'::text, 'sort'::text, 'export'::text, 'download'::text, 'record_view'::text, 'tab'::text, 'print'::text])))
);


--
-- Name: activity_events_id_seq; Type: SEQUENCE; Schema: curriculum; Owner: -
--

CREATE SEQUENCE curriculum.activity_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: activity_events_id_seq; Type: SEQUENCE OWNED BY; Schema: curriculum; Owner: -
--

ALTER SEQUENCE curriculum.activity_events_id_seq OWNED BY curriculum.activity_events.id;


--
-- Name: coaches; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.coaches (
    id character varying(128) NOT NULL,
    name character varying(255) NOT NULL,
    email character varying(255) DEFAULT ''::character varying NOT NULL,
    phone character varying(64) DEFAULT ''::character varying NOT NULL,
    job_title character varying(255) DEFAULT ''::character varying NOT NULL,
    assigned_group_ids jsonb,
    notes text DEFAULT ''::text NOT NULL,
    is_archived boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: cohorts; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.cohorts (
    cohort_id character varying(128) CONSTRAINT cohort_authoring_details_cohort_id_not_null NOT NULL,
    cohort_name character varying(500) DEFAULT ''::character varying CONSTRAINT cohort_authoring_details_cohort_name_not_null NOT NULL,
    programme_id character varying(255),
    programme_name character varying(255),
    start_date date,
    end_date date,
    duration_months integer DEFAULT 0 CONSTRAINT cohort_authoring_details_duration_months_not_null NOT NULL,
    color character varying(32),
    status character varying(32) DEFAULT 'planned'::character varying CONSTRAINT cohort_authoring_details_status_not_null NOT NULL,
    training_plan_ids jsonb,
    group_ids jsonb,
    module_names jsonb,
    holiday_ids jsonb,
    selected_holidays jsonb,
    holidays_in_range jsonb,
    holiday_summary jsonb,
    notes text,
    source_type character varying(64) DEFAULT 'training_plan'::character varying CONSTRAINT cohort_authoring_details_source_type_not_null NOT NULL,
    source_id character varying(128),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT cohort_authoring_details_created_at_not_null NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT cohort_authoring_details_updated_at_not_null NOT NULL,
    is_programme_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255),
    epa_months integer,
    apprenticeship_end_date date,
    apprenticeship_end_override date,
    excluded_holiday_ids jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: components; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.components (
    id character varying(128) CONSTRAINT module_authoring_components_id_not_null NOT NULL,
    week_id character varying(128) CONSTRAINT module_authoring_components_week_id_not_null NOT NULL,
    module_catalogue_id character varying(128) CONSTRAINT module_authoring_components_module_catalogue_id_not_null NOT NULL,
    type character varying(64) CONSTRAINT module_authoring_components_type_not_null NOT NULL,
    title text DEFAULT ''::character varying CONSTRAINT module_authoring_components_title_not_null NOT NULL,
    description text,
    expected_otjh numeric(8,2) DEFAULT 2 CONSTRAINT module_authoring_components_expected_otjh_not_null NOT NULL,
    points integer DEFAULT 0 CONSTRAINT module_authoring_components_points_not_null NOT NULL,
    reflection_required boolean DEFAULT false CONSTRAINT module_authoring_components_reflection_required_not_null NOT NULL,
    workplace_evidence_required boolean DEFAULT false CONSTRAINT module_authoring_components_workplace_evidence_require_not_null NOT NULL,
    tutor_validation_required boolean DEFAULT false CONSTRAINT module_authoring_components_tutor_validation_required_not_null NOT NULL,
    display_order integer DEFAULT 0 CONSTRAINT module_authoring_components_display_order_not_null NOT NULL,
    settings_json jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_components_created_at_not_null NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_components_updated_at_not_null NOT NULL,
    live_sessions_link character varying,
    is_programme_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255),
    ksb_mappings jsonb DEFAULT '[]'::jsonb NOT NULL,
    library_state character varying(16) DEFAULT ''::character varying NOT NULL,
    detached_at timestamp with time zone,
    origin_module_catalogue_id character varying(128),
    origin_module_title text,
    origin_week_id character varying(128),
    origin_week_label text,
    copied_from_id character varying(128),
    "Reflection_Question" text,
    coach_validation_required boolean DEFAULT true NOT NULL,
    CONSTRAINT curriculum_components_ksb_mappings_array_chk CHECK ((jsonb_typeof(ksb_mappings) = 'array'::text)),
    CONSTRAINT curriculum_components_ksb_weight_class_chk CHECK ((NOT jsonb_path_exists(COALESCE(ksb_mappings, '[]'::jsonb), '$[*]?(!(exists (@."weight_class")) || !((@."weight_class" == "hard" || @."weight_class" == "soft") || @."weight_class" == "possible"))'::jsonpath)))
);


--
-- Name: groups; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.groups (
    group_id character varying(128) NOT NULL,
    group_name character varying(500) DEFAULT ''::character varying NOT NULL,
    cohort_id character varying(128) DEFAULT ''::character varying NOT NULL,
    cohort_name character varying(500) DEFAULT ''::character varying NOT NULL,
    programme_id character varying(255) DEFAULT ''::character varying NOT NULL,
    programme_name character varying(255) DEFAULT ''::character varying NOT NULL,
    module_ids jsonb,
    module_names jsonb,
    coach_name character varying(255),
    notes text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    color character varying(32),
    session_week_day character varying(255),
    session_start_time character varying(32),
    session_end_time character varying(32),
    is_programme_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255)
);


--
-- Name: modules; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.modules (
    module_catalogue_id character varying(128) CONSTRAINT module_authoring_modules_module_catalogue_id_not_null NOT NULL,
    programme_id character varying(255),
    programme_name character varying(255),
    title text CONSTRAINT module_authoring_modules_title_not_null NOT NULL,
    description text,
    total_otjh numeric(8,2) DEFAULT 0 CONSTRAINT module_authoring_modules_total_otjh_not_null NOT NULL,
    quality_score integer DEFAULT 0 CONSTRAINT module_authoring_modules_quality_score_not_null NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_modules_created_at_not_null NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_modules_updated_at_not_null NOT NULL,
    sessions_number integer DEFAULT 0 CONSTRAINT module_authoring_modules_sessions_number_not_null NOT NULL,
    start_date date,
    end_date date,
    cohort_id character varying(255),
    cohort_name character varying(255),
    group_id character varying(255),
    group_name character varying(255),
    tutor_name character varying(255),
    color character varying(32),
    session_week_day character varying(255),
    session_start_time character varying(32),
    session_end_time character varying(32),
    ksb_profile_source_id character varying(255),
    source_type character varying(64),
    source_id character varying(128),
    coach_name character varying(255),
    is_programme_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255),
    weeks_number integer,
    tutor_email character varying(320),
    cover_image_url text,
    weekly_schedule jsonb,
    session_holidays jsonb,
    session_overrides jsonb DEFAULT '{}'::jsonb NOT NULL,
    learner_roster_mode character varying(32) DEFAULT 'inherited'::character varying NOT NULL,
    teams_shared_source_module_id character varying(128),
    ksb_remap_report text
);


--
-- Name: programmes; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.programmes (
    programme_id character varying(150) CONSTRAINT training_plan_program_configs_id_not_null NOT NULL,
    name character varying(255) CONSTRAINT training_plan_program_configs_name_not_null NOT NULL,
    color character varying(32) CONSTRAINT training_plan_program_configs_color_not_null NOT NULL,
    created_at timestamp with time zone CONSTRAINT training_plan_program_configs_created_at_not_null NOT NULL,
    updated_at timestamp with time zone CONSTRAINT training_plan_program_configs_updated_at_not_null NOT NULL,
    is_archived boolean,
    level character varying(64),
    description text,
    ksb_profile_source_id character varying(128),
    standard character varying(255),
    owner character varying(255),
    created_by character varying(255),
    is_active boolean,
    structure_type character varying(32),
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255),
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    required_otjh numeric(8,2),
    display_order integer DEFAULT 0
);


--
-- Name: weeks; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.weeks (
    id character varying(128) CONSTRAINT module_authoring_weeks_id_not_null NOT NULL,
    module_catalogue_id character varying(128) CONSTRAINT module_authoring_weeks_module_catalogue_id_not_null NOT NULL,
    week_number integer DEFAULT 1 CONSTRAINT module_authoring_weeks_week_number_not_null NOT NULL,
    title text DEFAULT ''::character varying CONSTRAINT module_authoring_weeks_title_not_null NOT NULL,
    summary text,
    learning_outcomes jsonb,
    display_order integer DEFAULT 0 CONSTRAINT module_authoring_weeks_display_order_not_null NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_weeks_created_at_not_null NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_weeks_updated_at_not_null NOT NULL,
    is_programme_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255),
    library_state character varying(16) DEFAULT ''::character varying NOT NULL,
    detached_at timestamp with time zone,
    origin_module_catalogue_id character varying(128),
    origin_module_title text,
    origin_week_id character varying(128),
    origin_week_label text,
    copied_from_id character varying(128),
    holiday_note_enabled boolean DEFAULT false NOT NULL,
    holiday_note text DEFAULT ''::text NOT NULL
);


--
-- Name: ksb_mappings; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.ksb_mappings (
    id character varying(128) CONSTRAINT module_authoring_ksb_mappings_id_not_null NOT NULL,
    module_catalogue_id character varying(128) CONSTRAINT module_authoring_ksb_mappings_module_catalogue_id_not_null NOT NULL,
    week_id character varying(128),
    component_id character varying(128),
    ksb_id character varying(255),
    ksb_code character varying(64) CONSTRAINT module_authoring_ksb_mappings_ksb_code_not_null NOT NULL,
    ksb_description text,
    classification character varying(32) DEFAULT 'secondary'::character varying CONSTRAINT module_authoring_ksb_mappings_classification_not_null NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_ksb_mappings_created_at_not_null NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT module_authoring_ksb_mappings_updated_at_not_null NOT NULL,
    weight numeric(5,2) DEFAULT 0 CONSTRAINT module_authoring_ksb_mappings_weight_not_null NOT NULL,
    source_type character varying(32),
    source_id character varying(255),
    is_programme_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255),
    weight_class character varying(32) DEFAULT 'soft'::character varying NOT NULL,
    CONSTRAINT curriculum_ksb_mappings_weight_class_chk CHECK (((weight_class)::text = ANY ((ARRAY['hard'::character varying, 'soft'::character varying, 'possible'::character varying])::text[])))
);


--
-- Name: england_holidays; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.england_holidays (
    id character varying(128) NOT NULL,
    division character varying(64) DEFAULT 'england-and-wales'::character varying NOT NULL,
    title character varying(255) NOT NULL,
    holiday_date date NOT NULL,
    notes character varying(255) DEFAULT ''::character varying NOT NULL,
    bunting boolean DEFAULT false NOT NULL,
    fetched_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: free_course_weeks; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.free_course_weeks (
    id character varying(128) NOT NULL,
    course_id character varying(128) NOT NULL,
    week_id character varying(128),
    course_name text NOT NULL,
    description text,
    cover_image_url text,
    week_number integer DEFAULT 1 NOT NULL,
    week_title text DEFAULT ''::character varying NOT NULL,
    display_order integer DEFAULT 0 NOT NULL,
    component_count integer DEFAULT 0 NOT NULL,
    total_otjh numeric(8,2) DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: free_courses; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.free_courses (
    id character varying(128) CONSTRAINT free_programme_modules_id_not_null NOT NULL,
    course_name text CONSTRAINT free_programme_modules_title_not_null NOT NULL,
    description text,
    display_order integer DEFAULT 0 CONSTRAINT free_programme_modules_display_order_not_null NOT NULL,
    component_count integer DEFAULT 0 CONSTRAINT free_programme_modules_component_count_not_null NOT NULL,
    total_otjh numeric(8,2) DEFAULT 0 CONSTRAINT free_programme_modules_total_otjh_not_null NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT free_programme_modules_created_at_not_null NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT free_programme_modules_updated_at_not_null NOT NULL,
    cover_image_url text,
    week_count integer DEFAULT 0 NOT NULL,
    week_builder_week_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    course_type character varying(16) DEFAULT 'free'::character varying NOT NULL,
    CONSTRAINT free_courses_course_type_check CHECK (((course_type)::text = ANY ((ARRAY['free'::character varying, 'apprentices'::character varying])::text[])))
);


--
-- Name: free_programme_components; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.free_programme_components (
    id character varying(128) NOT NULL,
    free_module_id character varying(128) NOT NULL,
    week_id character varying(128),
    programme_id character varying(255) NOT NULL,
    type character varying(64) NOT NULL,
    title text DEFAULT ''::character varying NOT NULL,
    description text,
    expected_otjh numeric(8,2) DEFAULT 2 NOT NULL,
    points integer DEFAULT 0 NOT NULL,
    reflection_required boolean DEFAULT false NOT NULL,
    workplace_evidence_required boolean DEFAULT false NOT NULL,
    tutor_validation_required boolean DEFAULT false NOT NULL,
    display_order integer DEFAULT 0 NOT NULL,
    settings_json jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: free_programme_modules; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.free_programme_modules (
    id character varying(128) CONSTRAINT free_programme_modules_id_not_null1 NOT NULL,
    programme_id character varying(255) NOT NULL,
    programme_name character varying(255),
    title character varying(500) CONSTRAINT free_programme_modules_title_not_null1 NOT NULL,
    description text,
    status character varying(32) DEFAULT 'draft'::character varying NOT NULL,
    color character varying(32),
    display_order integer DEFAULT 0 CONSTRAINT free_programme_modules_display_order_not_null1 NOT NULL,
    component_count integer DEFAULT 0 CONSTRAINT free_programme_modules_component_count_not_null1 NOT NULL,
    total_otjh numeric(8,2) DEFAULT 0 CONSTRAINT free_programme_modules_total_otjh_not_null1 NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT free_programme_modules_created_at_not_null1 NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP CONSTRAINT free_programme_modules_updated_at_not_null1 NOT NULL
);


--
-- Name: holidays; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.holidays (
    id bigint CONSTRAINT training_plan_holidays_id_not_null NOT NULL,
    label text CONSTRAINT training_plan_holidays_label_not_null NOT NULL,
    start_date date CONSTRAINT training_plan_holidays_start_date_not_null NOT NULL,
    end_date date CONSTRAINT training_plan_holidays_end_date_not_null NOT NULL,
    type character varying(50) CONSTRAINT training_plan_holidays_type_not_null NOT NULL,
    color character varying(32) CONSTRAINT training_plan_holidays_color_not_null NOT NULL,
    created_at timestamp with time zone CONSTRAINT training_plan_holidays_created_at_not_null NOT NULL,
    updated_at timestamp with time zone CONSTRAINT training_plan_holidays_updated_at_not_null NOT NULL,
    notes text,
    is_archived boolean DEFAULT false NOT NULL
);


--
-- Name: ksb_definitions; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.ksb_definitions (
    id bigint NOT NULL,
    profile_version_id bigint NOT NULL,
    "position" integer NOT NULL,
    code character varying(100) NOT NULL,
    number character varying(100) DEFAULT ''::character varying NOT NULL,
    ksb_type character varying(100) DEFAULT ''::character varying NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    CONSTRAINT ksb_definitions_position_check CHECK (("position" > 0))
);


--
-- Name: ksb_definitions_id_seq; Type: SEQUENCE; Schema: curriculum; Owner: -
--

CREATE SEQUENCE curriculum.ksb_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ksb_definitions_id_seq; Type: SEQUENCE OWNED BY; Schema: curriculum; Owner: -
--

ALTER SEQUENCE curriculum.ksb_definitions_id_seq OWNED BY curriculum.ksb_definitions.id;


--
-- Name: ksb_profile_versions; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.ksb_profile_versions (
    id bigint NOT NULL,
    source_profile_id character varying(255) NOT NULL,
    version_hash character varying(64) NOT NULL,
    programme text DEFAULT ''::text NOT NULL,
    definition_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ksb_profile_versions_definition_count_check CHECK ((definition_count >= 0))
);


--
-- Name: ksb_profile_versions_id_seq; Type: SEQUENCE; Schema: curriculum; Owner: -
--

CREATE SEQUENCE curriculum.ksb_profile_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ksb_profile_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: curriculum; Owner: -
--

ALTER SEQUENCE curriculum.ksb_profile_versions_id_seq OWNED BY curriculum.ksb_profile_versions.id;


--
-- Name: ksb_profiles; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.ksb_profiles (
    id character varying(128) NOT NULL,
    name character varying(255) NOT NULL,
    description text NOT NULL,
    knowledge_codes jsonb NOT NULL,
    skill_codes jsonb NOT NULL,
    behaviour_codes jsonb NOT NULL,
    is_active boolean NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    ksb_items jsonb NOT NULL,
    ksb_profile_id character varying(128),
    programme_ids jsonb,
    standard_source_id character varying(128)
);


--
-- Name: module_details; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.module_details (
    module_catalogue_id character varying(128) NOT NULL,
    background text,
    epa_requirements jsonb,
    professional_qualification_outcomes jsonb,
    intent text,
    learner_benefit text,
    employer_benefit text,
    sequence_purpose text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    deleted_via_parent character varying(255),
    ai_material jsonb
);


--
-- Name: record_revisions; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.record_revisions (
    id bigint NOT NULL,
    entity_type character varying(32) NOT NULL,
    entity_id character varying(128) NOT NULL,
    revision_no integer NOT NULL,
    action character varying(16) NOT NULL,
    module_catalogue_id character varying(128) DEFAULT ''::character varying NOT NULL,
    parent_id character varying(128) DEFAULT ''::character varying NOT NULL,
    title text DEFAULT ''::character varying NOT NULL,
    version_label character varying(32) DEFAULT ''::character varying NOT NULL,
    content_status character varying(64) DEFAULT ''::character varying NOT NULL,
    snapshot jsonb NOT NULL,
    changed_fields jsonb DEFAULT '[]'::jsonb NOT NULL,
    actor_email character varying(255) DEFAULT ''::character varying NOT NULL,
    actor_name character varying(255) DEFAULT ''::character varying NOT NULL,
    reason character varying(255) DEFAULT ''::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_type character varying(32) DEFAULT ''::character varying NOT NULL,
    triggered_by_email character varying(255) DEFAULT ''::character varying NOT NULL,
    triggered_by_name character varying(255) DEFAULT ''::character varying NOT NULL,
    source character varying(64) DEFAULT ''::character varying NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: record_revisions_id_seq; Type: SEQUENCE; Schema: curriculum; Owner: -
--

CREATE SEQUENCE curriculum.record_revisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: record_revisions_id_seq; Type: SEQUENCE OWNED BY; Schema: curriculum; Owner: -
--

ALTER SEQUENCE curriculum.record_revisions_id_seq OWNED BY curriculum.record_revisions.id;


--
-- Name: record_versions; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.record_versions (
    id bigint NOT NULL,
    entity_type character varying(32) NOT NULL,
    entity_id character varying(128) NOT NULL,
    version_label character varying(32) NOT NULL,
    revision_id bigint NOT NULL,
    content_status character varying(64) DEFAULT ''::character varying NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    actor_email character varying(255) DEFAULT ''::character varying NOT NULL,
    actor_name character varying(255) DEFAULT ''::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: record_versions_id_seq; Type: SEQUENCE; Schema: curriculum; Owner: -
--

CREATE SEQUENCE curriculum.record_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: record_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: curriculum; Owner: -
--

ALTER SEQUENCE curriculum.record_versions_id_seq OWNED BY curriculum.record_versions.id;


--
-- Name: standard_ksbs; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.standard_ksbs (
    id bigint CONSTRAINT skills_england_ksbs_id_not_null NOT NULL,
    source_url text,
    standard_title text,
    status text,
    level text,
    degree text,
    standard_ref text CONSTRAINT skills_england_ksbs_standard_ref_not_null NOT NULL,
    standard_version text CONSTRAINT skills_england_ksbs_standard_version_not_null NOT NULL,
    date_updated text,
    approved_for_delivery text,
    route text,
    typical_duration text,
    minimum_hours_for_compliance text,
    maximum_funding text,
    lars_code text,
    eqa_provider text,
    ksb_code text CONSTRAINT skills_england_ksbs_ksb_code_not_null NOT NULL,
    ksb_type text CONSTRAINT skills_england_ksbs_ksb_type_not_null NOT NULL,
    ksb_description text CONSTRAINT skills_england_ksbs_ksb_description_not_null NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: skills_england_ksbs_id_seq; Type: SEQUENCE; Schema: curriculum; Owner: -
--

CREATE SEQUENCE curriculum.skills_england_ksbs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: skills_england_ksbs_id_seq; Type: SEQUENCE OWNED BY; Schema: curriculum; Owner: -
--

ALTER SEQUENCE curriculum.skills_england_ksbs_id_seq OWNED BY curriculum.standard_ksbs.id;


--
-- Name: training_plan_holidays_id_seq; Type: SEQUENCE; Schema: curriculum; Owner: -
--

ALTER TABLE curriculum.holidays ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME curriculum.training_plan_holidays_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: tutors; Type: TABLE; Schema: curriculum; Owner: -
--

CREATE TABLE curriculum.tutors (
    id character varying(128) NOT NULL,
    name character varying(255) NOT NULL,
    email character varying(255) DEFAULT ''::character varying NOT NULL,
    phone character varying(64) DEFAULT ''::character varying NOT NULL,
    job_title character varying(255) DEFAULT ''::character varying NOT NULL,
    assigned_module_ids jsonb,
    notes text DEFAULT ''::text NOT NULL,
    is_archived boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: Apprenticeship_Agreements; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Apprenticeship_Agreements" (
    id uuid NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Apprentice_name" text,
    "Employer_name" text,
    "Employer_address" text,
    "Standard" text,
    "Start_date" date,
    "End_date" date,
    "Practical_start" date,
    "Practical_end" date,
    "Duration_weeks" numeric(8,1),
    "Planned_otjh" numeric(10,2),
    "Plan_modules" jsonb,
    "Apprentice_signature" text,
    "Apprentice_signed_name" text,
    "Apprentice_signed_at" timestamp with time zone,
    "Employer_signature" text,
    "Employer_signed_name" text,
    "Employer_signed_at" timestamp with time zone,
    "Fully_signed" boolean DEFAULT false NOT NULL,
    "Container" character varying(128),
    "Blob_name" character varying(512),
    "Doc_path" text,
    "Size_bytes" bigint,
    "Status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Created_users_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

ALTER TABLE enrolment."Created_users" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME enrolment."Created_users_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Eligibility_Form_Layouts; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Eligibility_Form_Layouts" (
    id bigint NOT NULL,
    "Layout" jsonb NOT NULL,
    "Created_by" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Eligibility_Form_Layouts_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Eligibility_Form_Layouts_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Eligibility_Form_Layouts_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Eligibility_Form_Layouts_id_seq" OWNED BY enrolment."Eligibility_Form_Layouts".id;


--
-- Name: Employers; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Employers" (
    id integer NOT NULL,
    "First_name" text,
    "Surname" text,
    "Gender" text,
    "Email" text,
    "Mobile" text,
    "Post_code" text,
    "Address_1" text,
    "Address_2" text,
    "Town_City" text,
    "County" text,
    "Country" text,
    "Employer_group_ids" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "Employer_group_names" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Signature" text,
    "Signature_name" text,
    "Signature_date" timestamp with time zone,
    uuid uuid DEFAULT gen_random_uuid() NOT NULL
);


--
-- Name: Employers_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

ALTER TABLE enrolment."Employers" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME enrolment."Employers_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Enrolment_Documents; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Enrolment_Documents" (
    id uuid NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_name" text,
    "Doc_type" character varying(64) NOT NULL,
    "Doc_name" character varying(512) NOT NULL,
    "Container" character varying(128) NOT NULL,
    "Blob_name" character varying(1024) NOT NULL,
    "Doc_path" text NOT NULL,
    "Content_type" character varying(255),
    "Size_bytes" bigint,
    "Signed" boolean DEFAULT false NOT NULL,
    "Generated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Employer_signature" text,
    "Employer_signed_name" text,
    "Employer_signed_at" timestamp with time zone,
    "Learner_signature" text,
    "Learner_signed_name" text,
    "Learner_signed_at" timestamp with time zone,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Enrolment_Reviews; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Enrolment_Reviews" (
    id bigint NOT NULL,
    "Event_key" text NOT NULL,
    "Review_type" text NOT NULL,
    "Review_label" text,
    "Learner_kind" text NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_name" text,
    "Learner_email" text,
    "Coach_id" bigint,
    "Coach_name" text,
    "Coach_email" text,
    "Scheduled_date" date,
    "Scheduled_time" time without time zone,
    "Duration_minutes" integer DEFAULT 60 NOT NULL,
    "Status" text DEFAULT 'booked'::text NOT NULL,
    "Notes" text,
    "Meeting_provider" text,
    "Meeting_link" text,
    "Graph_event_id" text,
    "Invite_sent" boolean DEFAULT false NOT NULL,
    "Sync_error" text,
    "Booked_at" timestamp with time zone,
    "Cancelled_at" timestamp with time zone,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Form_answers" jsonb,
    "Section_status" jsonb,
    "Form_completed" boolean DEFAULT false NOT NULL,
    "Form_completed_at" timestamp with time zone,
    "Reviewed_by" text,
    "Started_at" timestamp with time zone,
    "Learner_signature" text,
    "Learner_signed_name" text,
    "Learner_signed_at" timestamp with time zone,
    "Admin_signature" text,
    "Admin_signed_name" text,
    "Admin_signed_at" timestamp with time zone,
    "Employer_signature" text,
    "Employer_signed_name" text,
    "Employer_signed_at" timestamp with time zone,
    "Employer_signature_required" boolean
);


--
-- Name: Enrolment_Reviews_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Enrolment_Reviews_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Enrolment_Reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Enrolment_Reviews_id_seq" OWNED BY enrolment."Enrolment_Reviews".id;


--
-- Name: Extended_ILR; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Extended_ILR" (
    id bigint NOT NULL,
    "Learner_kind" text NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_name" text,
    "Answers" jsonb,
    "Learner_signed" boolean DEFAULT false NOT NULL,
    "Learner_signed_date" text,
    "Provider_signed" boolean DEFAULT false NOT NULL,
    "Provider_signed_date" text,
    "Completed" boolean DEFAULT false NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Wizard_draft" jsonb,
    "Next_of_kin_postcode" text,
    "Next_of_kin_address" text,
    "Eligibility_evidence" jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: Extended_ILR_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Extended_ILR_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Extended_ILR_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Extended_ILR_id_seq" OWNED BY enrolment."Extended_ILR".id;


--
-- Name: ILR_Documents; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."ILR_Documents" (
    id uuid NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_details" jsonb,
    "Answers" jsonb,
    "Learner_signature" text,
    "Learner_signed_name" text,
    "Learner_signed_at" timestamp with time zone,
    "Provider_signature" text,
    "Provider_signed_name" text,
    "Provider_signed_at" timestamp with time zone,
    "Fully_signed" boolean DEFAULT false NOT NULL,
    "Status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Organisations; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Organisations" (
    id integer NOT NULL,
    "Status" text,
    "Name" text,
    "Owner" text,
    "Category" text,
    "Group_type" text,
    "Parent_name" text,
    "EDRS_ERN_number" text,
    "Apprenticeship_agreement_id" text,
    "Post_code" text,
    "Address_1" text,
    "Address_2" text,
    "City_Town" text,
    "County" text,
    "Country" text,
    "Working_hours" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "Contact_name" text,
    "Contact_email" text,
    "Contact_telephone" text,
    "Contact_role" text,
    "Website" text,
    "Reference_number" text,
    "Levy_payer" text,
    "Approx_no_of_employees" integer,
    "Health_and_safety" text,
    "Logo_url" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Send_hours_verification_emails" boolean
);


--
-- Name: Organisations_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

ALTER TABLE enrolment."Organisations" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME enrolment."Organisations_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Review_Eligibility; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Review_Eligibility" (
    id bigint NOT NULL,
    "Review_id" bigint NOT NULL,
    "Event_key" text NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_name" text,
    "Completed" boolean DEFAULT false NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Over_16" text,
    "Within_contract_time" text,
    "PAYE_scheme" text,
    "Eligible_residency" text,
    "Identity_documents_seen" text,
    "Eligibility_evidence" text,
    "Right_to_work_England" text,
    "Fifty_percent_England" text,
    "Minimum_wage" text,
    "Initial_assessments" jsonb,
    "Diagnostic_assessments" jsonb,
    "Exemption_English" text,
    "Exemption_Maths" text,
    "Exemption_ICT" text,
    "FS_results" jsonb,
    "Holds_level_2" text,
    "Level_matches_role" text,
    "Productive_purpose" text,
    "KSB_exposure" text,
    "Release_for_OTJ" text,
    "Embed_OTJ" text,
    "Warning_areas" text,
    "Comments" text,
    "Programme_status" text
);


--
-- Name: Review_Eligibility_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Review_Eligibility_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Review_Eligibility_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Review_Eligibility_id_seq" OWNED BY enrolment."Review_Eligibility".id;


--
-- Name: Review_Health_Safety; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Review_Health_Safety" (
    id bigint NOT NULL,
    "Review_id" bigint NOT NULL,
    "Event_key" text NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_name" text,
    "Completed" boolean DEFAULT false NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Basic_arrangements" text,
    "Day_one_induction" text,
    "Fire_safety" text,
    "First_aid" text,
    "Supervision" text,
    "PPE" text,
    "Accident_recording" text,
    "Inform_changes" text,
    "HS_policy" text,
    "Liability_insurance" text
);


--
-- Name: Review_Health_Safety_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Review_Health_Safety_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Review_Health_Safety_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Review_Health_Safety_id_seq" OWNED BY enrolment."Review_Health_Safety".id;


--
-- Name: Review_RPL; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Review_RPL" (
    id bigint NOT NULL,
    "Review_id" bigint NOT NULL,
    "Event_key" text NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_name" text,
    "Completed" boolean DEFAULT false NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Prior_learning_items" jsonb,
    "Apprenticeship_appropriate" text,
    "Plan_aligns_standard" text,
    "Prior_education" text,
    "Prior_work_experience" text,
    "Plan_needs_adjusting" text,
    "ULN" text,
    "Reported_attainment" text,
    "Attainment_English" text,
    "Attainment_Maths" text,
    "Attainment_ICT" text,
    "Skills_radar_notes" text,
    "Comments" text
);


--
-- Name: Review_RPL_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Review_RPL_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Review_RPL_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Review_RPL_id_seq" OWNED BY enrolment."Review_RPL".id;


--
-- Name: Review_ULN_Privacy; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Review_ULN_Privacy" (
    id bigint NOT NULL,
    "Review_id" bigint NOT NULL,
    "Event_key" text NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Learner_name" text,
    "Completed" boolean DEFAULT false NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Privacy_notice_read" text,
    "Learner_acknowledged" text
);


--
-- Name: Review_ULN_Privacy_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Review_ULN_Privacy_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Review_ULN_Privacy_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Review_ULN_Privacy_id_seq" OWNED BY enrolment."Review_ULN_Privacy".id;


--
-- Name: Staff_users; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Staff_users" (
    id integer NOT NULL,
    "Username" text,
    "Email" text,
    "Phone_number" text,
    "Type" text,
    " Status" text,
    "Position" text,
    "Title" text,
    "Preferred_name" text,
    "Gender" text,
    "Date_of_birth" text,
    "Orgnization" text,
    "Case_owner" text,
    "Learning_provider" text,
    "Reference_number" text,
    "Invite_to_platform" boolean,
    "Allow_access_to_checkpoint" boolean,
    "Allow_access_to_console" boolean,
    "Allow_access_to_classic" boolean,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Access" text,
    uuid uuid DEFAULT gen_random_uuid() NOT NULL,
    "Access_extra" text,
    "Saved_signature" text,
    "Saved_signature_name" text,
    "Saved_signature_at" timestamp with time zone,
    "Signature" text,
    CONSTRAINT staff_users_access_check CHECK ((("Access" IS NULL) OR (lower(btrim("Access")) = ANY (ARRAY['enrolment'::text, 'curriculum'::text, 'coach'::text, 'tutor'::text, 'super-admin'::text, 'record-monitor'::text, 'advanced-admin'::text])))),
    CONSTRAINT staff_users_access_extra_check CHECK ((("Access_extra" IS NULL) OR (btrim("Access_extra") = ''::text) OR (string_to_array(replace(lower(btrim("Access_extra")), ' '::text, ''::text), ','::text) <@ ARRAY['enrolment'::text, 'curriculum'::text, 'coach'::text, 'tutor'::text, 'record-monitor'::text, 'super-admin'::text, 'advanced-admin'::text])))
);


--
-- Name: Staff_users_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

ALTER TABLE enrolment."Staff_users" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME enrolment."Staff_users_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Training_Plan_Documents; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Training_Plan_Documents" (
    id uuid NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Programme" jsonb,
    "Employment" jsonb,
    "Learning_plan" jsonb,
    "Otjh" jsonb,
    "Epa" jsonb,
    "Contacts" jsonb,
    "Apprentice_signature" text,
    "Apprentice_signed_name" text,
    "Apprentice_position" text,
    "Apprentice_signed_at" timestamp with time zone,
    "Employer_signature" text,
    "Employer_signed_name" text,
    "Employer_position" text,
    "Employer_signed_at" timestamp with time zone,
    "Provider_signature" text,
    "Provider_signed_name" text,
    "Provider_position" text,
    "Provider_signed_at" timestamp with time zone,
    "Fully_signed" boolean DEFAULT false NOT NULL,
    "Status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Wizard_Cv_Job; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Cv_Job" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Cv_file" text,
    "Experience_text" text,
    "PM_qualifications" text,
    "Functional_skills_enrol" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Highest_qualification" text,
    "Highest_qualification_field" text,
    "Has_field_qualification" boolean,
    "Highest_field_qualification" text,
    "Gcse_english" boolean,
    "Gcse_maths" boolean,
    "Documents" jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: Wizard_Cv_Job_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Cv_Job_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Cv_Job_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Cv_Job_id_seq" OWNED BY enrolment."Wizard_Cv_Job".id;


--
-- Name: Wizard_Form_Layouts; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Form_Layouts" (
    id bigint NOT NULL,
    "Layout" jsonb NOT NULL,
    "Created_by" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Wizard_Form_Layouts_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Form_Layouts_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Form_Layouts_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Form_Layouts_id_seq" OWNED BY enrolment."Wizard_Form_Layouts".id;


--
-- Name: Wizard_Ilr_Learner_Details; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Ilr_Learner_Details" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Years_at_address" integer,
    "At_address_since_birth" boolean,
    "Postcode_prior_to_enrolment" text,
    "National_insurance_number" text,
    "Ni_number_applied" boolean,
    "Legal_sex" text,
    "Pronouns" text,
    "Ethnicity" text,
    "Long_term_disability" boolean,
    "Highest_qualification" text,
    "Employment_status" text,
    "Full_time_education" boolean,
    "State_benefits" text,
    "Declaration_signature" text,
    "Declaration_signed_date" date,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Employment_start_date" date,
    "Job_title" text,
    "Self_employed" boolean,
    "Expected_leaving_date" date,
    "Length_of_unemployment" text,
    "Volunteers" boolean,
    "Benefit_claim_basis" text
);


--
-- Name: Wizard_Ilr_Learner_Details_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Ilr_Learner_Details_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Ilr_Learner_Details_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Ilr_Learner_Details_id_seq" OWNED BY enrolment."Wizard_Ilr_Learner_Details".id;


--
-- Name: Wizard_Introduction; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Introduction" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Custom_test_dropdoen" text,
    "Custom_sure" text,
    "Custom_sdfsdf" text,
    "Custom_dfssfs" text,
    "Custom_sdfsdfsdf" text
);


--
-- Name: Wizard_Introduction_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Introduction_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Introduction_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Introduction_id_seq" OWNED BY enrolment."Wizard_Introduction".id;


--
-- Name: Wizard_Ksb_Assessments; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Ksb_Assessments" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Ksb_id" text NOT NULL,
    "Level" text,
    "Note" text,
    "Action_text" text,
    "Action" text,
    "Goal" text,
    "Due_date" date,
    "Evidence_files" jsonb,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Score" integer
);


--
-- Name: Wizard_Ksb_Assessments_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Ksb_Assessments_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Ksb_Assessments_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Ksb_Assessments_id_seq" OWNED BY enrolment."Wizard_Ksb_Assessments".id;


--
-- Name: Wizard_Personal_Details; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Personal_Details" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "First_name" text,
    "Last_name" text,
    "Email" text,
    "Phone" text,
    "Address" text,
    "Date_of_birth" date,
    "Age" integer,
    "Sex" text,
    "Signature" text,
    "Signature_date" date,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Wizard_Personal_Details_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Personal_Details_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Personal_Details_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Personal_Details_id_seq" OWNED BY enrolment."Wizard_Personal_Details".id;


--
-- Name: Wizard_Plr; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Plr" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "ULN" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Wizard_Plr_Records; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Plr_Records" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Record_ref" text NOT NULL,
    "Place_of_study" text,
    "Qualification_type" text,
    "Subject" text,
    "Level" text,
    "Award_date" date,
    "Credits" integer,
    "Grade" text,
    "Record_type" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Start_date" date,
    "End_date" date,
    "Evidence" jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: Wizard_Plr_Records_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Plr_Records_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Plr_Records_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Plr_Records_id_seq" OWNED BY enrolment."Wizard_Plr_Records".id;


--
-- Name: Wizard_Plr_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Plr_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Plr_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Plr_id_seq" OWNED BY enrolment."Wizard_Plr".id;


--
-- Name: Wizard_Policy_Acks; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Policy_Acks" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Policy_id" text NOT NULL,
    "Acknowledged" boolean DEFAULT false NOT NULL,
    "Acknowledged_at" timestamp with time zone,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Wizard_Policy_Acks_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Policy_Acks_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Policy_Acks_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Policy_Acks_id_seq" OWNED BY enrolment."Wizard_Policy_Acks".id;


--
-- Name: Wizard_Skills_Radar; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Wizard_Skills_Radar" (
    id bigint NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Standard_id" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Wizard_Skills_Radar_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment."Wizard_Skills_Radar_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: Wizard_Skills_Radar_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment."Wizard_Skills_Radar_id_seq" OWNED BY enrolment."Wizard_Skills_Radar".id;


--
-- Name: Written_Agreements; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment."Written_Agreements" (
    id uuid NOT NULL,
    "Learner_kind" character varying(32) NOT NULL,
    "Learner_id" bigint NOT NULL,
    "Particulars" jsonb,
    "Delivery" jsonb,
    "Epa" jsonb,
    "Costs" jsonb,
    "Contacts" jsonb,
    "Learner_signature" text,
    "Learner_signed_name" text,
    "Learner_position" text,
    "Learner_signed_at" timestamp with time zone,
    "Employer_signature" text,
    "Employer_signed_name" text,
    "Employer_position" text,
    "Employer_signed_at" timestamp with time zone,
    "Provider_signature" text,
    "Provider_signed_name" text,
    "Provider_position" text,
    "Provider_signed_at" timestamp with time zone,
    "Fully_signed" boolean DEFAULT false NOT NULL,
    "Status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eligibility_form; Type: TABLE; Schema: enrolment; Owner: -
--

CREATE TABLE enrolment.eligibility_form (
    id bigint NOT NULL,
    first_name text NOT NULL,
    surname text NOT NULL,
    work_email text NOT NULL,
    phone_number text,
    full_address text,
    postcode text,
    postcode_country text,
    postcode_region text,
    postcode_district text,
    date_of_birth date,
    employment_status text,
    majority_hours_in_england boolean,
    employer_name text,
    employer_address text,
    employer_postcode text,
    employer_postcode_country text,
    employer_postcode_region text,
    employer_postcode_district text,
    representative_name text,
    representative_email text,
    lives_in_uk boolean,
    residence_not_for_education boolean,
    residency_status text,
    programme text,
    attended_funded_training boolean,
    funded_training_completed_on date,
    not_previously_completed_programme boolean,
    layout_version bigint,
    eligible boolean,
    ineligible_reason text,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eligibility_form_id_seq; Type: SEQUENCE; Schema: enrolment; Owner: -
--

CREATE SEQUENCE enrolment.eligibility_form_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: eligibility_form_id_seq; Type: SEQUENCE OWNED BY; Schema: enrolment; Owner: -
--

ALTER SEQUENCE enrolment.eligibility_form_id_seq OWNED BY enrolment.eligibility_form.id;


--
-- Name: Login_accounts; Type: TABLE; Schema: login; Owner: -
--

CREATE TABLE login."Login_accounts" (
    id bigint NOT NULL,
    "Subject_type" text NOT NULL,
    "Subject_id" bigint NOT NULL,
    "Email" text NOT NULL,
    "Display_name" text,
    "Role" text NOT NULL,
    "Password_hash" text DEFAULT ''::text NOT NULL,
    "Password_set_at" timestamp with time zone,
    "Is_active" boolean DEFAULT true NOT NULL,
    "Failed_attempts" integer DEFAULT 0 NOT NULL,
    "Locked_until" timestamp with time zone,
    "Last_login_at" timestamp with time zone,
    "Last_login_ip" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Updated_at" timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT login_accounts_role_check CHECK (("Role" = ANY (ARRAY['admin'::text, 'staff'::text, 'employer'::text, 'learner'::text]))),
    CONSTRAINT login_accounts_subject_type_check CHECK (("Subject_type" = ANY (ARRAY['learner'::text, 'employer'::text, 'staff'::text])))
);


--
-- Name: Invitations; Type: TABLE; Schema: login; Owner: -
--

CREATE TABLE login."Invitations" (
    id bigint NOT NULL,
    "Account_id" bigint NOT NULL,
    "Token_hash" character varying(64) NOT NULL,
    "Email" text NOT NULL,
    "Expires_at" timestamp with time zone NOT NULL,
    "Used_at" timestamp with time zone,
    "Invited_by" text,
    "Sent_at" timestamp with time zone,
    "Send_error" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Created_ip" text,
    "Acknowledged_at" timestamp with time zone,
    "Acknowledged_by" text
);


--
-- Name: Invitations_id_seq; Type: SEQUENCE; Schema: login; Owner: -
--

ALTER TABLE login."Invitations" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME login."Invitations_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Login_accounts_id_seq; Type: SEQUENCE; Schema: login; Owner: -
--

ALTER TABLE login."Login_accounts" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME login."Login_accounts_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Login_audit; Type: TABLE; Schema: login; Owner: -
--

CREATE TABLE login."Login_audit" (
    id bigint NOT NULL,
    "Event" text NOT NULL,
    "Email" text,
    "Account_id" bigint,
    "Succeeded" boolean DEFAULT false NOT NULL,
    "Reason" text,
    "Ip_address" text,
    "User_agent" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Login_audit_id_seq; Type: SEQUENCE; Schema: login; Owner: -
--

ALTER TABLE login."Login_audit" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME login."Login_audit_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Login_sessions; Type: TABLE; Schema: login; Owner: -
--

CREATE TABLE login."Login_sessions" (
    id bigint NOT NULL,
    "Account_id" bigint NOT NULL,
    "Token_hash" character varying(64) NOT NULL,
    "Expires_at" timestamp with time zone NOT NULL,
    "Revoked_at" timestamp with time zone,
    "Ip_address" text,
    "User_agent" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Last_seen_at" timestamp with time zone,
    "Remember" boolean DEFAULT false NOT NULL
);


--
-- Name: Login_sessions_id_seq; Type: SEQUENCE; Schema: login; Owner: -
--

ALTER TABLE login."Login_sessions" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME login."Login_sessions_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: Password_resets; Type: TABLE; Schema: login; Owner: -
--

CREATE TABLE login."Password_resets" (
    id bigint NOT NULL,
    "Account_id" bigint NOT NULL,
    "Token_hash" character varying(64) NOT NULL,
    "Email" text NOT NULL,
    "Expires_at" timestamp with time zone NOT NULL,
    "Used_at" timestamp with time zone,
    "Sent_at" timestamp with time zone,
    "Send_error" text,
    "Created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "Created_ip" text,
    "Acknowledged_at" timestamp with time zone,
    "Acknowledged_by" text
);


--
-- Name: Password_resets_id_seq; Type: SEQUENCE; Schema: login; Owner: -
--

ALTER TABLE login."Password_resets" ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME login."Password_resets_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: coach_directory; Type: TABLE; Schema: login; Owner: -
--

CREATE TABLE login.coach_directory (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    slug character varying(255) NOT NULL,
    links jsonb DEFAULT '{}'::jsonb NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: coach_directory_id_seq; Type: SEQUENCE; Schema: login; Owner: -
--

ALTER TABLE login.coach_directory ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME login.coach_directory_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: calendar_busy_slots id; Type: DEFAULT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".calendar_busy_slots ALTER COLUMN id SET DEFAULT nextval('"Learner".calendar_busy_slots_id_seq'::regclass);


--
-- Name: calendar_connections id; Type: DEFAULT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".calendar_connections ALTER COLUMN id SET DEFAULT nextval('"Learner".calendar_connections_id_seq'::regclass);


--
-- Name: reviews id; Type: DEFAULT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".reviews ALTER COLUMN id SET DEFAULT nextval('"Learner".reviews_id_seq'::regclass);


--
-- Name: activity_events id; Type: DEFAULT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.activity_events ALTER COLUMN id SET DEFAULT nextval('curriculum.activity_events_id_seq'::regclass);


--
-- Name: ksb_definitions id; Type: DEFAULT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_definitions ALTER COLUMN id SET DEFAULT nextval('curriculum.ksb_definitions_id_seq'::regclass);


--
-- Name: ksb_profile_versions id; Type: DEFAULT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_profile_versions ALTER COLUMN id SET DEFAULT nextval('curriculum.ksb_profile_versions_id_seq'::regclass);


--
-- Name: record_revisions id; Type: DEFAULT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.record_revisions ALTER COLUMN id SET DEFAULT nextval('curriculum.record_revisions_id_seq'::regclass);


--
-- Name: record_versions id; Type: DEFAULT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.record_versions ALTER COLUMN id SET DEFAULT nextval('curriculum.record_versions_id_seq'::regclass);


--
-- Name: standard_ksbs id; Type: DEFAULT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.standard_ksbs ALTER COLUMN id SET DEFAULT nextval('curriculum.skills_england_ksbs_id_seq'::regclass);


--
-- Name: Eligibility_Form_Layouts id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Eligibility_Form_Layouts" ALTER COLUMN id SET DEFAULT nextval('enrolment."Eligibility_Form_Layouts_id_seq"'::regclass);


--
-- Name: Enrolment_Reviews id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Enrolment_Reviews" ALTER COLUMN id SET DEFAULT nextval('enrolment."Enrolment_Reviews_id_seq"'::regclass);


--
-- Name: Extended_ILR id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Extended_ILR" ALTER COLUMN id SET DEFAULT nextval('enrolment."Extended_ILR_id_seq"'::regclass);


--
-- Name: Review_Eligibility id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_Eligibility" ALTER COLUMN id SET DEFAULT nextval('enrolment."Review_Eligibility_id_seq"'::regclass);


--
-- Name: Review_Health_Safety id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_Health_Safety" ALTER COLUMN id SET DEFAULT nextval('enrolment."Review_Health_Safety_id_seq"'::regclass);


--
-- Name: Review_RPL id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_RPL" ALTER COLUMN id SET DEFAULT nextval('enrolment."Review_RPL_id_seq"'::regclass);


--
-- Name: Review_ULN_Privacy id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_ULN_Privacy" ALTER COLUMN id SET DEFAULT nextval('enrolment."Review_ULN_Privacy_id_seq"'::regclass);


--
-- Name: Wizard_Cv_Job id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Cv_Job" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Cv_Job_id_seq"'::regclass);


--
-- Name: Wizard_Form_Layouts id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Form_Layouts" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Form_Layouts_id_seq"'::regclass);


--
-- Name: Wizard_Ilr_Learner_Details id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Ilr_Learner_Details" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Ilr_Learner_Details_id_seq"'::regclass);


--
-- Name: Wizard_Introduction id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Introduction" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Introduction_id_seq"'::regclass);


--
-- Name: Wizard_Ksb_Assessments id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Ksb_Assessments" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Ksb_Assessments_id_seq"'::regclass);


--
-- Name: Wizard_Personal_Details id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Personal_Details" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Personal_Details_id_seq"'::regclass);


--
-- Name: Wizard_Plr id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Plr" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Plr_id_seq"'::regclass);


--
-- Name: Wizard_Plr_Records id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Plr_Records" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Plr_Records_id_seq"'::regclass);


--
-- Name: Wizard_Policy_Acks id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Policy_Acks" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Policy_Acks_id_seq"'::regclass);


--
-- Name: Wizard_Skills_Radar id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Skills_Radar" ALTER COLUMN id SET DEFAULT nextval('enrolment."Wizard_Skills_Radar_id_seq"'::regclass);


--
-- Name: eligibility_form id; Type: DEFAULT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment.eligibility_form ALTER COLUMN id SET DEFAULT nextval('enrolment.eligibility_form_id_seq'::regclass);


--
-- PostgreSQL database dump complete
--


--
-- PostgreSQL database dump
--


-- Dumped from database version 18.6 (fd34222)
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

--
-- Name: coach_imported_meeting_ownership aptem_owner_calendar_uniq; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_meeting_ownership
    ADD CONSTRAINT aptem_owner_calendar_uniq UNIQUE (calendar_event_id);


--
-- Name: coach_imported_meeting_ownership aptem_owner_provider_uniq; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_meeting_ownership
    ADD CONSTRAINT aptem_owner_provider_uniq UNIQUE (tenant_id, organizer_id, online_meeting_id);


--
-- Name: coach_calendar_color_preference coach_calendar_color_preference_pkey; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_calendar_color_preference
    ADD CONSTRAINT coach_calendar_color_preference_pkey PRIMARY KEY (id);


--
-- Name: coach_calendar_sequence coach_calendar_sequence_pkey; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_calendar_sequence
    ADD CONSTRAINT coach_calendar_sequence_pkey PRIMARY KEY (id);


--
-- Name: coach_calendar_sequence coach_calendar_sequence_scope_uniq; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_calendar_sequence
    ADD CONSTRAINT coach_calendar_sequence_scope_uniq UNIQUE (learner_id, event_type);


--
-- Name: coach_calendar_color_preference coach_color_pref_owner_scope_key_uniq; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_calendar_color_preference
    ADD CONSTRAINT coach_color_pref_owner_scope_key_uniq UNIQUE (owner_email, scope, scope_key);


--
-- Name: coach_imported_meeting_ownership coach_imported_meeting_ownership_pkey; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_meeting_ownership
    ADD CONSTRAINT coach_imported_meeting_ownership_pkey PRIMARY KEY (id);


--
-- Name: coach_imported_review_instance coach_imported_review_instance_pkey; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_review_instance
    ADD CONSTRAINT coach_imported_review_instance_pkey PRIMARY KEY (id);


--
-- Name: coach_imported_review_instance coach_imported_review_owner_event_unique; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_review_instance
    ADD CONSTRAINT coach_imported_review_owner_event_unique UNIQUE (owner_email, event_key);


--
-- Name: coach_migrated_review_template coach_migrated_review_template_pkey; Type: CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_migrated_review_template
    ADD CONSTRAINT coach_migrated_review_template_pkey PRIMARY KEY (id);


--
-- Name: calendar_busy_slots calendar_busy_slot_unique; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".calendar_busy_slots
    ADD CONSTRAINT calendar_busy_slot_unique UNIQUE (connection_id, source_hash);


--
-- Name: calendar_busy_slots calendar_busy_slots_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".calendar_busy_slots
    ADD CONSTRAINT calendar_busy_slots_pkey PRIMARY KEY (id);


--
-- Name: calendar_connections calendar_connections_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".calendar_connections
    ADD CONSTRAINT calendar_connections_pkey PRIMARY KEY (id);


--
-- Name: coach_calendar_event coach_api_coachcalendarevent_event_key_key; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".coach_calendar_event
    ADD CONSTRAINT coach_api_coachcalendarevent_event_key_key UNIQUE (event_key);


--
-- Name: coach_calendar_event coach_api_coachcalendarevent_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".coach_calendar_event
    ADD CONSTRAINT coach_api_coachcalendarevent_pkey PRIMARY KEY (id);


--
-- Name: calendar_connections learner_calendar_provider_unique; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".calendar_connections
    ADD CONSTRAINT learner_calendar_provider_unique UNIQUE (learner_kind, learner_id, provider);


--
-- Name: learner_free_courses learner_free_courses_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_free_courses
    ADD CONSTRAINT learner_free_courses_pkey PRIMARY KEY (id);


--
-- Name: learner_ksb_assignments learner_ksb_assignments_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_ksb_assignments
    ADD CONSTRAINT learner_ksb_assignments_pkey PRIMARY KEY (learner_id);


--
-- Name: learner_ksbs learner_ksbs_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_ksbs
    ADD CONSTRAINT learner_ksbs_pkey PRIMARY KEY (learner_id, category, code);


--
-- Name: learner_training_plan_components learner_training_plan_components_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_components
    ADD CONSTRAINT learner_training_plan_components_pkey PRIMARY KEY (id);


--
-- Name: learner_training_plan_components learner_training_plan_components_plan_week_id_position_key; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_components
    ADD CONSTRAINT learner_training_plan_components_plan_week_id_position_key UNIQUE (plan_week_id, "position");


--
-- Name: learner_training_plan_modules learner_training_plan_modules_learner_id_position_key; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_modules
    ADD CONSTRAINT learner_training_plan_modules_learner_id_position_key UNIQUE (learner_id, "position");


--
-- Name: learner_training_plan_modules learner_training_plan_modules_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_modules
    ADD CONSTRAINT learner_training_plan_modules_pkey PRIMARY KEY (id);


--
-- Name: learner_training_plan_weeks learner_training_plan_weeks_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_weeks
    ADD CONSTRAINT learner_training_plan_weeks_pkey PRIMARY KEY (id);


--
-- Name: learner_training_plan_weeks learner_training_plan_weeks_plan_module_id_position_key; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_weeks
    ADD CONSTRAINT learner_training_plan_weeks_plan_module_id_position_key UNIQUE (plan_module_id, "position");


--
-- Name: learners learners_email_normalized_uniq; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learners
    ADD CONSTRAINT learners_email_normalized_uniq UNIQUE (email_normalized);


--
-- Name: learners learners_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learners
    ADD CONSTRAINT learners_pkey PRIMARY KEY (id);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: activity_events activity_events_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.activity_events
    ADD CONSTRAINT activity_events_pkey PRIMARY KEY (id);


--
-- Name: components chk_authoring_component_otjh_nonnegative; Type: CHECK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE curriculum.components
    ADD CONSTRAINT chk_authoring_component_otjh_nonnegative CHECK ((expected_otjh >= (0)::numeric)) NOT VALID;


--
-- Name: components chk_authoring_component_points_nonnegative; Type: CHECK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE curriculum.components
    ADD CONSTRAINT chk_authoring_component_points_nonnegative CHECK ((points >= 0)) NOT VALID;


--
-- Name: weeks chk_authoring_week_order_nonnegative; Type: CHECK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE curriculum.weeks
    ADD CONSTRAINT chk_authoring_week_order_nonnegative CHECK (((display_order >= 0) AND (week_number >= 1))) NOT VALID;


--
-- Name: coaches coaches_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.coaches
    ADD CONSTRAINT coaches_pkey PRIMARY KEY (id);


--
-- Name: cohorts cohort_authoring_details_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.cohorts
    ADD CONSTRAINT cohort_authoring_details_pkey PRIMARY KEY (cohort_id);


--
-- Name: ksb_definitions curriculum_ksb_definitions_version_code_uniq; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_definitions
    ADD CONSTRAINT curriculum_ksb_definitions_version_code_uniq UNIQUE (profile_version_id, code);


--
-- Name: ksb_profile_versions curriculum_ksb_profile_versions_source_hash_uniq; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_profile_versions
    ADD CONSTRAINT curriculum_ksb_profile_versions_source_hash_uniq UNIQUE (source_profile_id, version_hash);


--
-- Name: england_holidays england_holidays_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.england_holidays
    ADD CONSTRAINT england_holidays_pkey PRIMARY KEY (id);


--
-- Name: free_course_weeks free_course_weeks_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.free_course_weeks
    ADD CONSTRAINT free_course_weeks_pkey PRIMARY KEY (id);


--
-- Name: free_programme_components free_programme_components_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.free_programme_components
    ADD CONSTRAINT free_programme_components_pkey PRIMARY KEY (id);


--
-- Name: free_courses free_programme_modules_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.free_courses
    ADD CONSTRAINT free_programme_modules_pkey PRIMARY KEY (id);


--
-- Name: free_programme_modules free_programme_modules_pkey1; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.free_programme_modules
    ADD CONSTRAINT free_programme_modules_pkey1 PRIMARY KEY (id);


--
-- Name: groups groups_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.groups
    ADD CONSTRAINT groups_pkey PRIMARY KEY (group_id);


--
-- Name: ksb_definitions ksb_definitions_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_definitions
    ADD CONSTRAINT ksb_definitions_pkey PRIMARY KEY (id);


--
-- Name: ksb_profile_versions ksb_profile_versions_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_profile_versions
    ADD CONSTRAINT ksb_profile_versions_pkey PRIMARY KEY (id);


--
-- Name: ksb_profiles ksb_profiles_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_profiles
    ADD CONSTRAINT ksb_profiles_pkey PRIMARY KEY (id);


--
-- Name: components module_authoring_components_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.components
    ADD CONSTRAINT module_authoring_components_pkey PRIMARY KEY (id);


--
-- Name: ksb_mappings module_authoring_ksb_mappings_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_mappings
    ADD CONSTRAINT module_authoring_ksb_mappings_pkey PRIMARY KEY (id);


--
-- Name: modules module_authoring_modules_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.modules
    ADD CONSTRAINT module_authoring_modules_pkey PRIMARY KEY (module_catalogue_id);


--
-- Name: weeks module_authoring_weeks_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.weeks
    ADD CONSTRAINT module_authoring_weeks_pkey PRIMARY KEY (id);


--
-- Name: module_details module_details_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.module_details
    ADD CONSTRAINT module_details_pkey PRIMARY KEY (module_catalogue_id);


--
-- Name: record_revisions record_revisions_entity_revision_key; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.record_revisions
    ADD CONSTRAINT record_revisions_entity_revision_key UNIQUE (entity_type, entity_id, revision_no);


--
-- Name: record_revisions record_revisions_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.record_revisions
    ADD CONSTRAINT record_revisions_pkey PRIMARY KEY (id);


--
-- Name: record_versions record_versions_entity_label_key; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.record_versions
    ADD CONSTRAINT record_versions_entity_label_key UNIQUE (entity_type, entity_id, version_label);


--
-- Name: record_versions record_versions_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.record_versions
    ADD CONSTRAINT record_versions_pkey PRIMARY KEY (id);


--
-- Name: standard_ksbs skills_england_ksbs_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.standard_ksbs
    ADD CONSTRAINT skills_england_ksbs_pkey PRIMARY KEY (id);


--
-- Name: standard_ksbs skills_england_ksbs_unique; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.standard_ksbs
    ADD CONSTRAINT skills_england_ksbs_unique UNIQUE (standard_ref, standard_version, ksb_code);


--
-- Name: holidays training_plan_holidays_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.holidays
    ADD CONSTRAINT training_plan_holidays_pkey PRIMARY KEY (id);


--
-- Name: programmes training_plan_program_configs_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.programmes
    ADD CONSTRAINT training_plan_program_configs_pkey PRIMARY KEY (programme_id);


--
-- Name: tutors tutors_pkey; Type: CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.tutors
    ADD CONSTRAINT tutors_pkey PRIMARY KEY (id);


--
-- Name: Apprenticeship_Agreements Apprenticeship_Agreements_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Apprenticeship_Agreements"
    ADD CONSTRAINT "Apprenticeship_Agreements_pkey" PRIMARY KEY (id);


--
-- Name: Created_users Created_users_id_aptem_key; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Created_users"
    ADD CONSTRAINT "Created_users_id_aptem_key" UNIQUE (id_aptem);


--
-- Name: Created_users Created_users_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Created_users"
    ADD CONSTRAINT "Created_users_pkey" PRIMARY KEY (id);


--
-- Name: Eligibility_Form_Layouts Eligibility_Form_Layouts_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Eligibility_Form_Layouts"
    ADD CONSTRAINT "Eligibility_Form_Layouts_pkey" PRIMARY KEY (id);


--
-- Name: Employers Employers_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Employers"
    ADD CONSTRAINT "Employers_pkey" PRIMARY KEY (id);


--
-- Name: Enrolment_Documents Enrolment_Documents_Blob_name_key; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Enrolment_Documents"
    ADD CONSTRAINT "Enrolment_Documents_Blob_name_key" UNIQUE ("Blob_name");


--
-- Name: Enrolment_Documents Enrolment_Documents_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Enrolment_Documents"
    ADD CONSTRAINT "Enrolment_Documents_pkey" PRIMARY KEY (id);


--
-- Name: Enrolment_Reviews Enrolment_Reviews_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Enrolment_Reviews"
    ADD CONSTRAINT "Enrolment_Reviews_pkey" PRIMARY KEY (id);


--
-- Name: Extended_ILR Extended_ILR_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Extended_ILR"
    ADD CONSTRAINT "Extended_ILR_pkey" PRIMARY KEY (id);


--
-- Name: ILR_Documents ILR_Documents_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."ILR_Documents"
    ADD CONSTRAINT "ILR_Documents_pkey" PRIMARY KEY (id);


--
-- Name: Organisations Organisations_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Organisations"
    ADD CONSTRAINT "Organisations_pkey" PRIMARY KEY (id);


--
-- Name: Review_Eligibility Review_Eligibility_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_Eligibility"
    ADD CONSTRAINT "Review_Eligibility_pkey" PRIMARY KEY (id);


--
-- Name: Review_Health_Safety Review_Health_Safety_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_Health_Safety"
    ADD CONSTRAINT "Review_Health_Safety_pkey" PRIMARY KEY (id);


--
-- Name: Review_RPL Review_RPL_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_RPL"
    ADD CONSTRAINT "Review_RPL_pkey" PRIMARY KEY (id);


--
-- Name: Review_ULN_Privacy Review_ULN_Privacy_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_ULN_Privacy"
    ADD CONSTRAINT "Review_ULN_Privacy_pkey" PRIMARY KEY (id);


--
-- Name: Staff_users Staff_users_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Staff_users"
    ADD CONSTRAINT "Staff_users_pkey" PRIMARY KEY (id);


--
-- Name: Training_Plan_Documents Training_Plan_Documents_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Training_Plan_Documents"
    ADD CONSTRAINT "Training_Plan_Documents_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Cv_Job Wizard_Cv_Job_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Cv_Job"
    ADD CONSTRAINT "Wizard_Cv_Job_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Form_Layouts Wizard_Form_Layouts_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Form_Layouts"
    ADD CONSTRAINT "Wizard_Form_Layouts_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Ilr_Learner_Details Wizard_Ilr_Learner_Details_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Ilr_Learner_Details"
    ADD CONSTRAINT "Wizard_Ilr_Learner_Details_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Introduction Wizard_Introduction_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Introduction"
    ADD CONSTRAINT "Wizard_Introduction_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Ksb_Assessments Wizard_Ksb_Assessments_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Ksb_Assessments"
    ADD CONSTRAINT "Wizard_Ksb_Assessments_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Personal_Details Wizard_Personal_Details_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Personal_Details"
    ADD CONSTRAINT "Wizard_Personal_Details_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Plr_Records Wizard_Plr_Records_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Plr_Records"
    ADD CONSTRAINT "Wizard_Plr_Records_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Plr Wizard_Plr_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Plr"
    ADD CONSTRAINT "Wizard_Plr_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Policy_Acks Wizard_Policy_Acks_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Policy_Acks"
    ADD CONSTRAINT "Wizard_Policy_Acks_pkey" PRIMARY KEY (id);


--
-- Name: Wizard_Skills_Radar Wizard_Skills_Radar_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Wizard_Skills_Radar"
    ADD CONSTRAINT "Wizard_Skills_Radar_pkey" PRIMARY KEY (id);


--
-- Name: Written_Agreements Written_Agreements_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Written_Agreements"
    ADD CONSTRAINT "Written_Agreements_pkey" PRIMARY KEY (id);


--
-- Name: eligibility_form eligibility_form_pkey; Type: CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment.eligibility_form
    ADD CONSTRAINT eligibility_form_pkey PRIMARY KEY (id);


--
-- Name: Invitations Invitations_Token_hash_key; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Invitations"
    ADD CONSTRAINT "Invitations_Token_hash_key" UNIQUE ("Token_hash");


--
-- Name: Invitations Invitations_pkey; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Invitations"
    ADD CONSTRAINT "Invitations_pkey" PRIMARY KEY (id);


--
-- Name: Login_accounts Login_accounts_pkey; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Login_accounts"
    ADD CONSTRAINT "Login_accounts_pkey" PRIMARY KEY (id);


--
-- Name: Login_audit Login_audit_pkey; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Login_audit"
    ADD CONSTRAINT "Login_audit_pkey" PRIMARY KEY (id);


--
-- Name: Login_sessions Login_sessions_Token_hash_key; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Login_sessions"
    ADD CONSTRAINT "Login_sessions_Token_hash_key" UNIQUE ("Token_hash");


--
-- Name: Login_sessions Login_sessions_pkey; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Login_sessions"
    ADD CONSTRAINT "Login_sessions_pkey" PRIMARY KEY (id);


--
-- Name: Password_resets Password_resets_Token_hash_key; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Password_resets"
    ADD CONSTRAINT "Password_resets_Token_hash_key" UNIQUE ("Token_hash");


--
-- Name: Password_resets Password_resets_pkey; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Password_resets"
    ADD CONSTRAINT "Password_resets_pkey" PRIMARY KEY (id);


--
-- Name: coach_directory coach_directory_pkey; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login.coach_directory
    ADD CONSTRAINT coach_directory_pkey PRIMARY KEY (id);


--
-- Name: coach_directory coach_directory_slug_key; Type: CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login.coach_directory
    ADD CONSTRAINT coach_directory_slug_key UNIQUE (slug);


--
-- Name: aptem_actual_session_call_uniq; Type: INDEX; Schema: Coach; Owner: -
--

CREATE UNIQUE INDEX aptem_actual_session_call_uniq ON "Coach".coach_imported_review_instance USING btree (((meeting_intelligence #>> '{actualSession,tenantId}'::text[])) COLLATE "C", ((meeting_intelligence #>> '{actualSession,callId}'::text[])) COLLATE "C") WHERE (meeting_intelligence ? 'actualSession'::text);


--
-- Name: aptem_actual_session_meeting_uniq; Type: INDEX; Schema: Coach; Owner: -
--

CREATE UNIQUE INDEX aptem_actual_session_meeting_uniq ON "Coach".coach_imported_review_instance USING btree (((meeting_intelligence #>> '{actualSession,tenantId}'::text[])) COLLATE "C", ((meeting_intelligence #>> '{actualSession,organizerId}'::text[])) COLLATE "C", ((meeting_intelligence #>> '{actualSession,onlineMeetingId}'::text[])) COLLATE "C") WHERE (meeting_intelligence ? 'actualSession'::text);


--
-- Name: aptem_owner_active_source_uniq; Type: INDEX; Schema: Coach; Owner: -
--

CREATE UNIQUE INDEX aptem_owner_active_source_uniq ON "Coach".coach_imported_meeting_ownership USING btree (source_review_id) WHERE ((state)::text = 'active'::text);


--
-- Name: coach_calendar_color_preference_owner_email_501b8ee9; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_calendar_color_preference_owner_email_501b8ee9 ON "Coach".coach_calendar_color_preference USING btree (owner_email);


--
-- Name: coach_calendar_color_preference_owner_email_501b8ee9_like; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_calendar_color_preference_owner_email_501b8ee9_like ON "Coach".coach_calendar_color_preference USING btree (owner_email varchar_pattern_ops);


--
-- Name: coach_color_owner_scope_idx; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_color_owner_scope_idx ON "Coach".coach_calendar_color_preference USING btree (owner_email, scope);


--
-- Name: coach_imported_meeting_ownership_overlay_id_42a261b3; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_meeting_ownership_overlay_id_42a261b3 ON "Coach".coach_imported_meeting_ownership USING btree (overlay_id);


--
-- Name: coach_imported_review_instance_learner_id_2830bef1; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_review_instance_learner_id_2830bef1 ON "Coach".coach_imported_review_instance USING btree (learner_id);


--
-- Name: coach_imported_review_instance_migrated_template_id_94f3657b; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_review_instance_migrated_template_id_94f3657b ON "Coach".coach_imported_review_instance USING btree (migrated_template_id);


--
-- Name: coach_imported_review_instance_owner_email_ed3a21a8; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_review_instance_owner_email_ed3a21a8 ON "Coach".coach_imported_review_instance USING btree (owner_email);


--
-- Name: coach_imported_review_instance_owner_email_ed3a21a8_like; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_review_instance_owner_email_ed3a21a8_like ON "Coach".coach_imported_review_instance USING btree (owner_email varchar_pattern_ops);


--
-- Name: coach_imported_review_instance_source_review_id_e98a0d87; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_review_instance_source_review_id_e98a0d87 ON "Coach".coach_imported_review_instance USING btree (source_review_id);


--
-- Name: coach_imported_review_instance_status_47de5fa8; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_review_instance_status_47de5fa8 ON "Coach".coach_imported_review_instance USING btree (status);


--
-- Name: coach_imported_review_instance_status_47de5fa8_like; Type: INDEX; Schema: Coach; Owner: -
--

CREATE INDEX coach_imported_review_instance_status_47de5fa8_like ON "Coach".coach_imported_review_instance USING btree (status varchar_pattern_ops);


--
-- Name: coach_imported_review_owner_event_ci_unique; Type: INDEX; Schema: Coach; Owner: -
--

CREATE UNIQUE INDEX coach_imported_review_owner_event_ci_unique ON "Coach".coach_imported_review_instance USING btree (lower((owner_email)::text), event_key);


--
-- Name: coach_imported_review_source_unique; Type: INDEX; Schema: Coach; Owner: -
--

CREATE UNIQUE INDEX coach_imported_review_source_unique ON "Coach".coach_imported_review_instance USING btree (source_review_id) WHERE (source_review_id IS NOT NULL);


--
-- Name: coach_migrated_global_active; Type: INDEX; Schema: Coach; Owner: -
--

CREATE UNIQUE INDEX coach_migrated_global_active ON "Coach".coach_migrated_review_template USING btree (scope, review_family) WHERE (is_active AND ((scope)::text = 'GLOBAL'::text));


--
-- Name: coach_migrated_programme_active; Type: INDEX; Schema: Coach; Owner: -
--

CREATE UNIQUE INDEX coach_migrated_programme_active ON "Coach".coach_migrated_review_template USING btree (scope, programme_key, review_family) WHERE (is_active AND ((scope)::text = 'PROGRAMME'::text));


--
-- Name: activity_ssot_learner_ksbs_source_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX activity_ssot_learner_ksbs_source_uniq ON "Learner".learner_ksbs USING btree (learner_id, category, code);


--
-- Name: calendar_busy_connection_time_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX calendar_busy_connection_time_idx ON "Learner".calendar_busy_slots USING btree (connection_id, starts_at, ends_at);


--
-- Name: coach_api_coachcalendarevent_event_key_c732ce7c_like; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_event_key_c732ce7c_like ON "Learner".coach_calendar_event USING btree (event_key varchar_pattern_ops);


--
-- Name: coach_api_coachcalendarevent_event_type_c56ac194; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_event_type_c56ac194 ON "Learner".coach_calendar_event USING btree (event_type);


--
-- Name: coach_api_coachcalendarevent_event_type_c56ac194_like; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_event_type_c56ac194_like ON "Learner".coach_calendar_event USING btree (event_type varchar_pattern_ops);


--
-- Name: coach_api_coachcalendarevent_learner_id_64ee9626; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_learner_id_64ee9626 ON "Learner".coach_calendar_event USING btree (learner_id);


--
-- Name: coach_api_coachcalendarevent_owner_email_1abc9c41; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_owner_email_1abc9c41 ON "Learner".coach_calendar_event USING btree (owner_email);


--
-- Name: coach_api_coachcalendarevent_owner_email_1abc9c41_like; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_owner_email_1abc9c41_like ON "Learner".coach_calendar_event USING btree (owner_email varchar_pattern_ops);


--
-- Name: coach_api_coachcalendarevent_status_71dd1626; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_status_71dd1626 ON "Learner".coach_calendar_event USING btree (status);


--
-- Name: coach_api_coachcalendarevent_status_71dd1626_like; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_status_71dd1626_like ON "Learner".coach_calendar_event USING btree (status varchar_pattern_ops);


--
-- Name: coach_api_coachcalendarevent_target_date_d089cbbe; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_api_coachcalendarevent_target_date_d089cbbe ON "Learner".coach_calendar_event USING btree (target_date);


--
-- Name: coach_calendar_booking_seq_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX coach_calendar_booking_seq_uniq ON "Learner".coach_calendar_event USING btree (learner_id, event_type, sequence) WHERE ((event_type)::text = ANY ((ARRAY['catch-up'::character varying, 'student-support'::character varying, 'eligibility-review'::character varying, 'workspace'::character varying, 'training-plan'::character varying, 'uln-privacy'::character varying])::text[]));


--
-- Name: coach_calendar_event_operation_id_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX coach_calendar_event_operation_id_uniq ON "Learner".coach_calendar_event USING btree (operation_id);


--
-- Name: coach_calendar_event_review_instance_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_calendar_event_review_instance_idx ON "Learner".coach_calendar_event USING btree (review_instance_id);


--
-- Name: coach_calendar_event_sync_state_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_calendar_event_sync_state_idx ON "Learner".coach_calendar_event USING btree (sync_state);


--
-- Name: coach_calendar_owner_idempotency_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX coach_calendar_owner_idempotency_uniq ON "Learner".coach_calendar_event USING btree (owner_email, idempotency_key) WHERE ((idempotency_key)::text <> ''::text);


--
-- Name: coach_learner_type_date_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_learner_type_date_idx ON "Learner".coach_calendar_event USING btree (learner_id, event_type, target_date);


--
-- Name: coach_owner_date_status_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX coach_owner_date_status_idx ON "Learner".coach_calendar_event USING btree (owner_email, target_date, status);


--
-- Name: learner_calendar_owner_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_calendar_owner_idx ON "Learner".calendar_connections USING btree (learner_kind, learner_id);


--
-- Name: learner_free_courses_learner_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_free_courses_learner_idx ON "Learner".learner_free_courses USING btree (learner_id);


--
-- Name: learner_ksb_assignments_profile_version_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_ksb_assignments_profile_version_idx ON "Learner".learner_ksb_assignments USING btree (profile_version_id);


--
-- Name: learner_ksbs_learner_id_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_ksbs_learner_id_idx ON "Learner".learner_ksbs USING btree (learner_id);


--
-- Name: learner_learners_aptem_id_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX learner_learners_aptem_id_uniq ON "Learner".learners USING btree (aptem_id) WHERE (aptem_id IS NOT NULL);


--
-- Name: learner_plan_components_curriculum_component_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_plan_components_curriculum_component_idx ON "Learner".learner_training_plan_components USING btree (curriculum_component_id);


--
-- Name: learner_plan_components_ref_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_plan_components_ref_idx ON "Learner".learner_training_plan_components USING btree (component_ref);


--
-- Name: learner_plan_modules_curriculum_module_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_plan_modules_curriculum_module_idx ON "Learner".learner_training_plan_modules USING btree (curriculum_module_id);


--
-- Name: learner_plan_modules_learner_ref_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_plan_modules_learner_ref_idx ON "Learner".learner_training_plan_modules USING btree (learner_id, module_ref);


--
-- Name: learner_plan_weeks_curriculum_week_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_plan_weeks_curriculum_week_idx ON "Learner".learner_training_plan_weeks USING btree (curriculum_week_id);


--
-- Name: learner_plan_weeks_ref_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_plan_weeks_ref_idx ON "Learner".learner_training_plan_weeks USING btree (week_ref);


--
-- Name: learner_reviews_aptem_review_id_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX learner_reviews_aptem_review_id_uniq ON "Learner".reviews USING btree (aptem_review_id) WHERE (aptem_review_id IS NOT NULL);


--
-- Name: learner_reviews_learner_id_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learner_reviews_learner_id_idx ON "Learner".reviews USING btree (learner_id);


--
-- Name: learners_coach_email_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learners_coach_email_idx ON "Learner".learners USING btree (lower(TRIM(BOTH FROM coach_email)));


--
-- Name: learners_cohort_id_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learners_cohort_id_idx ON "Learner".learners USING btree (cohort_id);


--
-- Name: learners_enrolment_id_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX learners_enrolment_id_uniq ON "Learner".learners USING btree (enrolment_id) WHERE (enrolment_id IS NOT NULL);


--
-- Name: learners_group_id_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learners_group_id_idx ON "Learner".learners USING btree (group_id);


--
-- Name: learners_lifecycle_status_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learners_lifecycle_status_idx ON "Learner".learners USING btree (lifecycle_status);


--
-- Name: learners_programme_status_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX learners_programme_status_idx ON "Learner".learners USING btree (programme_status);


--
-- Name: learners_uuid_uniq; Type: INDEX; Schema: Learner; Owner: -
--

CREATE UNIQUE INDEX learners_uuid_uniq ON "Learner".learners USING btree (uuid);


--
-- Name: perf_learner_coach_email_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX perf_learner_coach_email_idx ON "Learner".learners USING btree (lower(btrim((coach_email)::text)), full_name, id);


--
-- Name: perf_learner_lifecycle_programme_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX perf_learner_lifecycle_programme_idx ON "Learner".learners USING btree (lifecycle_status, programme, id);


--
-- Name: perf_plan_component_week_order_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX perf_plan_component_week_order_idx ON "Learner".learner_training_plan_components USING btree (plan_week_id, "position", id);


--
-- Name: perf_plan_module_learner_order_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX perf_plan_module_learner_order_idx ON "Learner".learner_training_plan_modules USING btree (learner_id, "position", id);


--
-- Name: perf_plan_week_module_order_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX perf_plan_week_module_order_idx ON "Learner".learner_training_plan_weeks USING btree (plan_module_id, "position", id);


--
-- Name: reviews_current_document_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX reviews_current_document_idx ON "Learner".reviews USING btree (current_document_id);


--
-- Name: reviews_learner_lifecycle_status_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX reviews_learner_lifecycle_status_idx ON "Learner".reviews USING btree (learner_lifecycle_status);


--
-- Name: reviews_modern_bucket_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX reviews_modern_bucket_idx ON "Learner".reviews USING btree (learner_id, modern_bucket);


--
-- Name: reviews_visibility_state_idx; Type: INDEX; Schema: Learner; Owner: -
--

CREATE INDEX reviews_visibility_state_idx ON "Learner".reviews USING btree (visibility_state);


--
-- Name: activity_events_actor_occurred_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX activity_events_actor_occurred_idx ON curriculum.activity_events USING btree (actor_email, occurred_at DESC);


--
-- Name: activity_events_occurred_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX activity_events_occurred_idx ON curriculum.activity_events USING btree (occurred_at DESC);


--
-- Name: activity_events_page_occurred_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX activity_events_page_occurred_idx ON curriculum.activity_events USING btree (page_key, occurred_at DESC);


--
-- Name: activity_events_visit_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX activity_events_visit_idx ON curriculum.activity_events USING btree (visit_id, occurred_at);


--
-- Name: activity_events_workspace_actor_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX activity_events_workspace_actor_idx ON curriculum.activity_events USING btree (workspace, actor_email, occurred_at DESC);


--
-- Name: activity_events_workspace_occurred_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX activity_events_workspace_occurred_idx ON curriculum.activity_events USING btree (workspace, occurred_at DESC);


--
-- Name: curriculum_coaches_name_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_coaches_name_idx ON curriculum.coaches USING btree (name);


--
-- Name: curriculum_cohorts_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_cohorts_deleted_at_idx ON curriculum.cohorts USING btree (deleted_at);


--
-- Name: curriculum_cohorts_programme_deleted_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_cohorts_programme_deleted_idx ON curriculum.cohorts USING btree (is_programme_deleted);


--
-- Name: curriculum_components_copied_from_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_copied_from_idx ON curriculum.components USING btree (copied_from_id) WHERE (copied_from_id IS NOT NULL);


--
-- Name: curriculum_components_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_deleted_at_idx ON curriculum.components USING btree (deleted_at);


--
-- Name: curriculum_components_library_state_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_library_state_idx ON curriculum.components USING btree (library_state) WHERE ((library_state)::text <> ''::text);


--
-- Name: curriculum_components_module_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_module_idx ON curriculum.components USING btree (module_catalogue_id);


--
-- Name: curriculum_components_module_week_order_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_module_week_order_idx ON curriculum.components USING btree (module_catalogue_id, week_id, display_order, id);


--
-- Name: curriculum_components_programme_deleted_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_programme_deleted_idx ON curriculum.components USING btree (is_programme_deleted);


--
-- Name: curriculum_components_updated_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_updated_idx ON curriculum.components USING btree (updated_at DESC);


--
-- Name: curriculum_components_week_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_components_week_idx ON curriculum.components USING btree (week_id);


--
-- Name: curriculum_free_course_weeks_course_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_free_course_weeks_course_idx ON curriculum.free_course_weeks USING btree (course_id);


--
-- Name: curriculum_free_courses_order_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_free_courses_order_idx ON curriculum.free_courses USING btree (display_order, id);


--
-- Name: curriculum_free_courses_week_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_free_courses_week_idx ON curriculum.free_course_weeks USING btree (week_id);


--
-- Name: curriculum_free_programme_components_week_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_free_programme_components_week_idx ON curriculum.free_programme_components USING btree (week_id);


--
-- Name: curriculum_groups_cohort_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_groups_cohort_idx ON curriculum.groups USING btree (cohort_id);


--
-- Name: curriculum_groups_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_groups_deleted_at_idx ON curriculum.groups USING btree (deleted_at);


--
-- Name: curriculum_groups_programme_cohort_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_groups_programme_cohort_idx ON curriculum.groups USING btree (programme_id, cohort_id);


--
-- Name: curriculum_groups_programme_deleted_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_groups_programme_deleted_idx ON curriculum.groups USING btree (is_programme_deleted);


--
-- Name: curriculum_groups_programme_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_groups_programme_idx ON curriculum.groups USING btree (programme_id);


--
-- Name: curriculum_ksb_definitions_version_position_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_ksb_definitions_version_position_idx ON curriculum.ksb_definitions USING btree (profile_version_id, "position");


--
-- Name: curriculum_ksb_mapping_component_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_ksb_mapping_component_idx ON curriculum.ksb_mappings USING btree (component_id);


--
-- Name: curriculum_ksb_mapping_component_lookup_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_ksb_mapping_component_lookup_idx ON curriculum.ksb_mappings USING btree (component_id, ksb_code, source_type, source_id);


--
-- Name: curriculum_ksb_mapping_component_unique_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE UNIQUE INDEX curriculum_ksb_mapping_component_unique_idx ON curriculum.ksb_mappings USING btree (component_id, upper((ksb_code)::text), COALESCE(source_type, ''::character varying), COALESCE(source_id, ''::character varying)) WHERE ((component_id IS NOT NULL) AND ((component_id)::text <> ''::text));


--
-- Name: curriculum_ksb_mapping_module_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_ksb_mapping_module_idx ON curriculum.ksb_mappings USING btree (module_catalogue_id);


--
-- Name: curriculum_ksb_mapping_week_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_ksb_mapping_week_idx ON curriculum.ksb_mappings USING btree (week_id);


--
-- Name: curriculum_ksb_mappings_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_ksb_mappings_deleted_at_idx ON curriculum.ksb_mappings USING btree (deleted_at);


--
-- Name: curriculum_ksb_mappings_programme_deleted_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_ksb_mappings_programme_deleted_idx ON curriculum.ksb_mappings USING btree (is_programme_deleted);


--
-- Name: curriculum_ksb_profiles_profile_id_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE UNIQUE INDEX curriculum_ksb_profiles_profile_id_idx ON curriculum.ksb_profiles USING btree (ksb_profile_id) WHERE ((ksb_profile_id IS NOT NULL) AND ((ksb_profile_id)::text <> ''::text));


--
-- Name: curriculum_module_details_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_module_details_deleted_at_idx ON curriculum.module_details USING btree (deleted_at);


--
-- Name: curriculum_modules_cohort_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_modules_cohort_idx ON curriculum.modules USING btree (cohort_id, module_catalogue_id);


--
-- Name: curriculum_modules_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_modules_deleted_at_idx ON curriculum.modules USING btree (deleted_at);


--
-- Name: curriculum_modules_group_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_modules_group_idx ON curriculum.modules USING btree (group_id, module_catalogue_id);


--
-- Name: curriculum_modules_programme_deleted_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_modules_programme_deleted_idx ON curriculum.modules USING btree (is_programme_deleted);


--
-- Name: curriculum_modules_programme_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_modules_programme_idx ON curriculum.modules USING btree (programme_id, module_catalogue_id);


--
-- Name: curriculum_modules_source_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_modules_source_idx ON curriculum.modules USING btree (source_id);


--
-- Name: curriculum_modules_title_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_modules_title_idx ON curriculum.modules USING btree (title);


--
-- Name: curriculum_programmes_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_programmes_deleted_at_idx ON curriculum.programmes USING btree (deleted_at);


--
-- Name: curriculum_programmes_status_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_programmes_status_idx ON curriculum.programmes USING btree (status);


--
-- Name: curriculum_tutors_name_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_tutors_name_idx ON curriculum.tutors USING btree (name);


--
-- Name: curriculum_weeks_copied_from_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_weeks_copied_from_idx ON curriculum.weeks USING btree (copied_from_id) WHERE (copied_from_id IS NOT NULL);


--
-- Name: curriculum_weeks_deleted_at_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_weeks_deleted_at_idx ON curriculum.weeks USING btree (deleted_at);


--
-- Name: curriculum_weeks_library_state_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_weeks_library_state_idx ON curriculum.weeks USING btree (library_state) WHERE ((library_state)::text <> ''::text);


--
-- Name: curriculum_weeks_module_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_weeks_module_idx ON curriculum.weeks USING btree (module_catalogue_id);


--
-- Name: curriculum_weeks_module_order_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_weeks_module_order_idx ON curriculum.weeks USING btree (module_catalogue_id, display_order, id);


--
-- Name: curriculum_weeks_programme_deleted_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX curriculum_weeks_programme_deleted_idx ON curriculum.weeks USING btree (is_programme_deleted);


--
-- Name: england_holidays_date_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX england_holidays_date_idx ON curriculum.england_holidays USING btree (holiday_date);


--
-- Name: england_holidays_division_date_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE UNIQUE INDEX england_holidays_division_date_idx ON curriculum.england_holidays USING btree (division, holiday_date);


--
-- Name: holidays_end_date_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX holidays_end_date_idx ON curriculum.holidays USING btree (end_date);


--
-- Name: holidays_start_date_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX holidays_start_date_idx ON curriculum.holidays USING btree (start_date);


--
-- Name: idx_authoring_components_module_week_order; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX idx_authoring_components_module_week_order ON curriculum.components USING btree (module_catalogue_id, week_id, display_order);


--
-- Name: idx_authoring_components_type; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX idx_authoring_components_type ON curriculum.components USING btree (type);


--
-- Name: idx_authoring_ksb_component; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX idx_authoring_ksb_component ON curriculum.ksb_mappings USING btree (module_catalogue_id, week_id, component_id);


--
-- Name: idx_authoring_weeks_module_order; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX idx_authoring_weeks_module_order ON curriculum.weeks USING btree (module_catalogue_id, display_order, week_number);


--
-- Name: ksb_profile_is_acti_2347e0_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX ksb_profile_is_acti_2347e0_idx ON curriculum.ksb_profiles USING btree (is_active, name);


--
-- Name: record_revisions_actor_created_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_revisions_actor_created_idx ON curriculum.record_revisions USING btree (actor_email, created_at DESC);


--
-- Name: record_revisions_created_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_revisions_created_idx ON curriculum.record_revisions USING btree (created_at DESC);


--
-- Name: record_revisions_entity_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_revisions_entity_idx ON curriculum.record_revisions USING btree (entity_type, entity_id, revision_no DESC);


--
-- Name: record_revisions_module_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_revisions_module_idx ON curriculum.record_revisions USING btree (module_catalogue_id, created_at DESC);


--
-- Name: record_revisions_page_workspace_created_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_revisions_page_workspace_created_idx ON curriculum.record_revisions USING btree (((metadata ->> 'page_workspace'::text)), created_at DESC);


--
-- Name: record_revisions_source_created_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_revisions_source_created_idx ON curriculum.record_revisions USING btree (source, created_at DESC) WHERE ((source)::text <> ''::text);


--
-- Name: record_revisions_type_created_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_revisions_type_created_idx ON curriculum.record_revisions USING btree (entity_type, created_at DESC);


--
-- Name: record_versions_entity_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_versions_entity_idx ON curriculum.record_versions USING btree (entity_type, entity_id, created_at DESC);


--
-- Name: record_versions_revision_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE INDEX record_versions_revision_idx ON curriculum.record_versions USING btree (revision_id);


--
-- Name: skills_england_ksbs_unique_idx; Type: INDEX; Schema: curriculum; Owner: -
--

CREATE UNIQUE INDEX skills_england_ksbs_unique_idx ON curriculum.standard_ksbs USING btree (standard_ref, standard_version, ksb_code);


--
-- Name: apprenticeship_agreements_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX apprenticeship_agreements_learner ON enrolment."Apprenticeship_Agreements" USING btree ("Learner_kind", "Learner_id");


--
-- Name: apprenticeship_agreements_one_active; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX apprenticeship_agreements_one_active ON enrolment."Apprenticeship_Agreements" USING btree ("Learner_kind", "Learner_id") WHERE (("Status")::text = 'active'::text);


--
-- Name: created_users_email_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX created_users_email_uniq ON enrolment."Created_users" USING btree (lower(btrim("Email"))) WHERE (COALESCE(btrim("Email"), ''::text) <> ''::text);


--
-- Name: created_users_learner_id_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX created_users_learner_id_uniq ON enrolment."Created_users" USING btree (learner_id) WHERE (learner_id IS NOT NULL);


--
-- Name: created_users_uuid_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX created_users_uuid_uniq ON enrolment."Created_users" USING btree (uuid);


--
-- Name: employers_uuid_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX employers_uuid_uniq ON enrolment."Employers" USING btree (uuid);


--
-- Name: enrolment_created_users_employer_id_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_created_users_employer_id_idx ON enrolment."Created_users" USING btree ("Employer_id");


--
-- Name: enrolment_employers_email_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_employers_email_idx ON enrolment."Employers" USING btree (lower(btrim("Email")));


--
-- Name: enrolment_employers_group_ids_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_employers_group_ids_idx ON enrolment."Employers" USING gin ("Employer_group_ids");


--
-- Name: enrolment_organisations_name_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_organisations_name_idx ON enrolment."Organisations" USING btree (lower(btrim("Name")));


--
-- Name: enrolment_organisations_status_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_organisations_status_idx ON enrolment."Organisations" USING btree ("Status");


--
-- Name: enrolment_reviews_coach_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_reviews_coach_idx ON enrolment."Enrolment_Reviews" USING btree ("Coach_email");


--
-- Name: enrolment_reviews_employer_signed_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_reviews_employer_signed_idx ON enrolment."Enrolment_Reviews" USING btree ("Employer_signed_at");


--
-- Name: enrolment_reviews_event_key_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX enrolment_reviews_event_key_uniq ON enrolment."Enrolment_Reviews" USING btree ("Event_key");


--
-- Name: enrolment_reviews_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_reviews_learner_idx ON enrolment."Enrolment_Reviews" USING btree ("Learner_kind", "Learner_id");


--
-- Name: enrolment_staff_users_email_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_staff_users_email_idx ON enrolment."Staff_users" USING btree (lower(btrim("Email")));


--
-- Name: enrolment_staff_users_position_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX enrolment_staff_users_position_idx ON enrolment."Staff_users" USING btree ("Position");


--
-- Name: extended_ilr_learner_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX extended_ilr_learner_uniq ON enrolment."Extended_ILR" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_enrolment_docs_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_enrolment_docs_learner ON enrolment."Enrolment_Documents" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_enrolment_docs_type; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_enrolment_docs_type ON enrolment."Enrolment_Documents" USING btree ("Doc_type");


--
-- Name: idx_wizard_cv_job_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_cv_job_learner ON enrolment."Wizard_Cv_Job" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_wizard_ilr_learner_details_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_ilr_learner_details_learner ON enrolment."Wizard_Ilr_Learner_Details" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_wizard_ksb_assessments_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_ksb_assessments_learner ON enrolment."Wizard_Ksb_Assessments" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_wizard_ksb_level; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_ksb_level ON enrolment."Wizard_Ksb_Assessments" USING btree ("Level");


--
-- Name: idx_wizard_personal_details_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_personal_details_learner ON enrolment."Wizard_Personal_Details" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_wizard_plr_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_plr_learner ON enrolment."Wizard_Plr" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_wizard_plr_records_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_plr_records_learner ON enrolment."Wizard_Plr_Records" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_wizard_policy_ack_policy; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_policy_ack_policy ON enrolment."Wizard_Policy_Acks" USING btree ("Policy_id");


--
-- Name: idx_wizard_policy_acks_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_policy_acks_learner ON enrolment."Wizard_Policy_Acks" USING btree ("Learner_kind", "Learner_id");


--
-- Name: idx_wizard_skills_radar_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX idx_wizard_skills_radar_learner ON enrolment."Wizard_Skills_Radar" USING btree ("Learner_kind", "Learner_id");


--
-- Name: ilr_documents_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX ilr_documents_learner ON enrolment."ILR_Documents" USING btree ("Learner_kind", "Learner_id");


--
-- Name: ilr_documents_one_active; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX ilr_documents_one_active ON enrolment."ILR_Documents" USING btree ("Learner_kind", "Learner_id") WHERE (("Status")::text = 'active'::text);


--
-- Name: perf_created_user_type_status_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_created_user_type_status_idx ON enrolment."Created_users" USING btree ("Learner_type", "Programme_status", id);


--
-- Name: perf_extended_ilr_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_extended_ilr_learner_idx ON enrolment."Extended_ILR" USING btree ("Learner_kind", "Learner_id");


--
-- Name: perf_staff_position_name_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_staff_position_name_idx ON enrolment."Staff_users" USING btree ("Position", "Username", id);


--
-- Name: perf_wizard_cv_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_wizard_cv_learner_idx ON enrolment."Wizard_Cv_Job" USING btree ("Learner_kind", "Learner_id");


--
-- Name: perf_wizard_ksb_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_wizard_ksb_learner_idx ON enrolment."Wizard_Ksb_Assessments" USING btree ("Learner_kind", "Learner_id", "Ksb_id");


--
-- Name: perf_wizard_personal_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_wizard_personal_learner_idx ON enrolment."Wizard_Personal_Details" USING btree ("Learner_kind", "Learner_id");


--
-- Name: perf_wizard_plr_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_wizard_plr_learner_idx ON enrolment."Wizard_Plr" USING btree ("Learner_kind", "Learner_id");


--
-- Name: perf_wizard_plr_record_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_wizard_plr_record_learner_idx ON enrolment."Wizard_Plr_Records" USING btree ("Learner_kind", "Learner_id", id);


--
-- Name: perf_wizard_policy_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_wizard_policy_learner_idx ON enrolment."Wizard_Policy_Acks" USING btree ("Learner_kind", "Learner_id", "Policy_id");


--
-- Name: perf_wizard_radar_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX perf_wizard_radar_learner_idx ON enrolment."Wizard_Skills_Radar" USING btree ("Learner_kind", "Learner_id");


--
-- Name: review_eligibility_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX review_eligibility_learner_idx ON enrolment."Review_Eligibility" USING btree ("Learner_id");


--
-- Name: review_eligibility_review_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX review_eligibility_review_uniq ON enrolment."Review_Eligibility" USING btree ("Review_id");


--
-- Name: review_health_safety_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX review_health_safety_learner_idx ON enrolment."Review_Health_Safety" USING btree ("Learner_id");


--
-- Name: review_health_safety_review_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX review_health_safety_review_uniq ON enrolment."Review_Health_Safety" USING btree ("Review_id");


--
-- Name: review_rpl_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX review_rpl_learner_idx ON enrolment."Review_RPL" USING btree ("Learner_id");


--
-- Name: review_rpl_review_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX review_rpl_review_uniq ON enrolment."Review_RPL" USING btree ("Review_id");


--
-- Name: review_uln_privacy_learner_idx; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX review_uln_privacy_learner_idx ON enrolment."Review_ULN_Privacy" USING btree ("Learner_id");


--
-- Name: review_uln_privacy_review_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX review_uln_privacy_review_uniq ON enrolment."Review_ULN_Privacy" USING btree ("Review_id");


--
-- Name: staff_users_uuid_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX staff_users_uuid_uniq ON enrolment."Staff_users" USING btree (uuid);


--
-- Name: training_plan_documents_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX training_plan_documents_learner ON enrolment."Training_Plan_Documents" USING btree ("Learner_kind", "Learner_id");


--
-- Name: training_plan_documents_one_active; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX training_plan_documents_one_active ON enrolment."Training_Plan_Documents" USING btree ("Learner_kind", "Learner_id") WHERE (("Status")::text = 'active'::text);


--
-- Name: wizard_cv_job_learner_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_cv_job_learner_uniq ON enrolment."Wizard_Cv_Job" USING btree ("Learner_kind", "Learner_id");


--
-- Name: wizard_ilr_learner_details_learner_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_ilr_learner_details_learner_uniq ON enrolment."Wizard_Ilr_Learner_Details" USING btree ("Learner_kind", "Learner_id");


--
-- Name: wizard_introduction_learner_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_introduction_learner_uniq ON enrolment."Wizard_Introduction" USING btree ("Learner_kind", "Learner_id");


--
-- Name: wizard_ksb_learner_ksb_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_ksb_learner_ksb_uniq ON enrolment."Wizard_Ksb_Assessments" USING btree ("Learner_kind", "Learner_id", "Ksb_id");


--
-- Name: wizard_personal_details_learner_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_personal_details_learner_uniq ON enrolment."Wizard_Personal_Details" USING btree ("Learner_kind", "Learner_id");


--
-- Name: wizard_plr_learner_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_plr_learner_uniq ON enrolment."Wizard_Plr" USING btree ("Learner_kind", "Learner_id");


--
-- Name: wizard_plr_record_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_plr_record_uniq ON enrolment."Wizard_Plr_Records" USING btree ("Learner_kind", "Learner_id", "Record_ref");


--
-- Name: wizard_policy_ack_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_policy_ack_uniq ON enrolment."Wizard_Policy_Acks" USING btree ("Learner_kind", "Learner_id", "Policy_id");


--
-- Name: wizard_skills_radar_learner_uniq; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX wizard_skills_radar_learner_uniq ON enrolment."Wizard_Skills_Radar" USING btree ("Learner_kind", "Learner_id");


--
-- Name: written_agreements_learner; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE INDEX written_agreements_learner ON enrolment."Written_Agreements" USING btree ("Learner_kind", "Learner_id");


--
-- Name: written_agreements_one_active; Type: INDEX; Schema: enrolment; Owner: -
--

CREATE UNIQUE INDEX written_agreements_one_active ON enrolment."Written_Agreements" USING btree ("Learner_kind", "Learner_id") WHERE (("Status")::text = 'active'::text);


--
-- Name: invitations_account_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX invitations_account_idx ON login."Invitations" USING btree ("Account_id");


--
-- Name: login_accounts_email_kind_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE UNIQUE INDEX login_accounts_email_kind_idx ON login."Login_accounts" USING btree ("Subject_type", lower(btrim("Email")));


--
-- Name: login_accounts_email_lookup_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX login_accounts_email_lookup_idx ON login."Login_accounts" USING btree (lower(btrim("Email")));


--
-- Name: login_accounts_subject_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE UNIQUE INDEX login_accounts_subject_idx ON login."Login_accounts" USING btree ("Subject_type", "Subject_id");


--
-- Name: login_audit_created_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX login_audit_created_idx ON login."Login_audit" USING btree ("Created_at" DESC);


--
-- Name: login_audit_email_created_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX login_audit_email_created_idx ON login."Login_audit" USING btree (lower(btrim("Email")), "Created_at" DESC);


--
-- Name: login_audit_ip_created_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX login_audit_ip_created_idx ON login."Login_audit" USING btree ("Ip_address", "Created_at" DESC);


--
-- Name: login_sessions_account_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX login_sessions_account_idx ON login."Login_sessions" USING btree ("Account_id");


--
-- Name: login_sessions_expiry_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX login_sessions_expiry_idx ON login."Login_sessions" USING btree ("Expires_at");


--
-- Name: password_resets_account_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX password_resets_account_idx ON login."Password_resets" USING btree ("Account_id");


--
-- Name: password_resets_email_created_idx; Type: INDEX; Schema: login; Owner: -
--

CREATE INDEX password_resets_email_created_idx ON login."Password_resets" USING btree (lower(btrim("Email")), "Created_at" DESC);


--
-- Name: coach_imported_review_instance aptem_actual_session_claim_guard; Type: TRIGGER; Schema: Coach; Owner: -
--

CREATE TRIGGER aptem_actual_session_claim_guard BEFORE INSERT OR DELETE OR UPDATE ON "Coach".coach_imported_review_instance FOR EACH ROW EXECUTE FUNCTION "Coach".aptem_actual_session_claim_guard();


--
-- Name: coach_imported_meeting_ownership aptem_imported_meeting_owner_guard; Type: TRIGGER; Schema: Coach; Owner: -
--

CREATE TRIGGER aptem_imported_meeting_owner_guard BEFORE DELETE OR UPDATE ON "Coach".coach_imported_meeting_ownership FOR EACH ROW EXECUTE FUNCTION "Coach".aptem_imported_meeting_owner_guard();


--
-- Name: coach_calendar_event aptem_owned_calendar_identity_guard; Type: TRIGGER; Schema: Learner; Owner: -
--

CREATE TRIGGER aptem_owned_calendar_identity_guard BEFORE DELETE OR UPDATE ON "Learner".coach_calendar_event FOR EACH ROW EXECUTE FUNCTION "Coach".aptem_owned_calendar_identity_guard();


--
-- Name: learners learners_set_updated_at; Type: TRIGGER; Schema: Learner; Owner: -
--

CREATE TRIGGER learners_set_updated_at BEFORE UPDATE ON "Learner".learners FOR EACH ROW EXECUTE FUNCTION "Learner".set_learners_updated_at();


--
-- Name: learners trg_sync_review_lifecycle_status; Type: TRIGGER; Schema: Learner; Owner: -
--



--
-- Name: coach_imported_meeting_ownership aptem_owner_calendar_fk; Type: FK CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_meeting_ownership
    ADD CONSTRAINT aptem_owner_calendar_fk FOREIGN KEY (calendar_event_id) REFERENCES "Learner".coach_calendar_event(id) DEFERRABLE;


--
-- Name: coach_imported_meeting_ownership aptem_owner_source_fk; Type: FK CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_meeting_ownership
    ADD CONSTRAINT aptem_owner_source_fk FOREIGN KEY (source_review_id) REFERENCES "Learner".reviews(id) DEFERRABLE;


--
-- Name: coach_imported_meeting_ownership coach_imported_meeti_overlay_id_42a261b3_fk_coach_imp; Type: FK CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_meeting_ownership
    ADD CONSTRAINT coach_imported_meeti_overlay_id_42a261b3_fk_coach_imp FOREIGN KEY (overlay_id) REFERENCES "Coach".coach_imported_review_instance(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: coach_imported_review_instance coach_imported_revie_migrated_template_id_94f3657b_fk_coach_mig; Type: FK CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_review_instance
    ADD CONSTRAINT coach_imported_revie_migrated_template_id_94f3657b_fk_coach_mig FOREIGN KEY (migrated_template_id) REFERENCES "Coach".coach_migrated_review_template(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: coach_imported_review_instance coach_imported_review_source_fk; Type: FK CONSTRAINT; Schema: Coach; Owner: -
--

ALTER TABLE ONLY "Coach".coach_imported_review_instance
    ADD CONSTRAINT coach_imported_review_source_fk FOREIGN KEY (source_review_id) REFERENCES "Learner".reviews(id) NOT VALID;


--
-- Name: calendar_busy_slots calendar_busy_slots_connection_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".calendar_busy_slots
    ADD CONSTRAINT calendar_busy_slots_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES "Learner".calendar_connections(id) ON DELETE CASCADE;


--
-- Name: learner_free_courses learner_free_courses_learner_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_free_courses
    ADD CONSTRAINT learner_free_courses_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES "Learner".learners(id) ON DELETE CASCADE;


--
-- Name: learner_ksb_assignments learner_ksb_assignments_learner_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_ksb_assignments
    ADD CONSTRAINT learner_ksb_assignments_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES "Learner".learners(id) ON DELETE CASCADE;


--
-- Name: learner_ksb_assignments learner_ksb_assignments_profile_version_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_ksb_assignments
    ADD CONSTRAINT learner_ksb_assignments_profile_version_id_fkey FOREIGN KEY (profile_version_id) REFERENCES curriculum.ksb_profile_versions(id) ON DELETE RESTRICT;


--
-- Name: learner_training_plan_components learner_training_plan_components_curriculum_component_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_components
    ADD CONSTRAINT learner_training_plan_components_curriculum_component_fkey FOREIGN KEY (curriculum_component_id) REFERENCES curriculum.components(id) ON DELETE RESTRICT;


--
-- Name: learner_training_plan_components learner_training_plan_components_plan_week_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_components
    ADD CONSTRAINT learner_training_plan_components_plan_week_id_fkey FOREIGN KEY (plan_week_id) REFERENCES "Learner".learner_training_plan_weeks(id) ON DELETE CASCADE;


--
-- Name: learner_training_plan_modules learner_training_plan_modules_curriculum_module_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_modules
    ADD CONSTRAINT learner_training_plan_modules_curriculum_module_fkey FOREIGN KEY (curriculum_module_id) REFERENCES curriculum.modules(module_catalogue_id) ON DELETE RESTRICT;


--
-- Name: learner_training_plan_modules learner_training_plan_modules_learner_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_modules
    ADD CONSTRAINT learner_training_plan_modules_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES "Learner".learners(id) ON DELETE CASCADE;


--
-- Name: learner_training_plan_weeks learner_training_plan_weeks_curriculum_week_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_weeks
    ADD CONSTRAINT learner_training_plan_weeks_curriculum_week_fkey FOREIGN KEY (curriculum_week_id) REFERENCES curriculum.weeks(id) ON DELETE RESTRICT;


--
-- Name: learner_training_plan_weeks learner_training_plan_weeks_plan_module_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learner_training_plan_weeks
    ADD CONSTRAINT learner_training_plan_weeks_plan_module_id_fkey FOREIGN KEY (plan_module_id) REFERENCES "Learner".learner_training_plan_modules(id) ON DELETE CASCADE;


--
-- Name: learners learners_enrolment_id_fkey; Type: FK CONSTRAINT; Schema: Learner; Owner: -
--

ALTER TABLE ONLY "Learner".learners
    ADD CONSTRAINT learners_enrolment_id_fkey FOREIGN KEY (enrolment_id) REFERENCES enrolment."Created_users"(id) ON DELETE RESTRICT;


--
-- Name: cohorts cohorts_programme_id_fkey; Type: FK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.cohorts
    ADD CONSTRAINT cohorts_programme_id_fkey FOREIGN KEY (programme_id) REFERENCES curriculum.programmes(programme_id) ON UPDATE RESTRICT ON DELETE RESTRICT NOT VALID;


--
-- Name: groups groups_programme_id_fkey; Type: FK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.groups
    ADD CONSTRAINT groups_programme_id_fkey FOREIGN KEY (programme_id) REFERENCES curriculum.programmes(programme_id) ON UPDATE RESTRICT ON DELETE RESTRICT NOT VALID;


--
-- Name: ksb_definitions ksb_definitions_profile_version_id_fkey; Type: FK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.ksb_definitions
    ADD CONSTRAINT ksb_definitions_profile_version_id_fkey FOREIGN KEY (profile_version_id) REFERENCES curriculum.ksb_profile_versions(id) ON DELETE CASCADE;


--
-- Name: modules modules_programme_id_fkey; Type: FK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.modules
    ADD CONSTRAINT modules_programme_id_fkey FOREIGN KEY (programme_id) REFERENCES curriculum.programmes(programme_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: record_versions record_versions_revision_id_fkey; Type: FK CONSTRAINT; Schema: curriculum; Owner: -
--

ALTER TABLE ONLY curriculum.record_versions
    ADD CONSTRAINT record_versions_revision_id_fkey FOREIGN KEY (revision_id) REFERENCES curriculum.record_revisions(id) ON DELETE CASCADE;


--
-- Name: Review_Eligibility Review_Eligibility_Review_id_fkey; Type: FK CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_Eligibility"
    ADD CONSTRAINT "Review_Eligibility_Review_id_fkey" FOREIGN KEY ("Review_id") REFERENCES enrolment."Enrolment_Reviews"(id) ON DELETE CASCADE;


--
-- Name: Review_Health_Safety Review_Health_Safety_Review_id_fkey; Type: FK CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_Health_Safety"
    ADD CONSTRAINT "Review_Health_Safety_Review_id_fkey" FOREIGN KEY ("Review_id") REFERENCES enrolment."Enrolment_Reviews"(id) ON DELETE CASCADE;


--
-- Name: Review_RPL Review_RPL_Review_id_fkey; Type: FK CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_RPL"
    ADD CONSTRAINT "Review_RPL_Review_id_fkey" FOREIGN KEY ("Review_id") REFERENCES enrolment."Enrolment_Reviews"(id) ON DELETE CASCADE;


--
-- Name: Review_ULN_Privacy Review_ULN_Privacy_Review_id_fkey; Type: FK CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Review_ULN_Privacy"
    ADD CONSTRAINT "Review_ULN_Privacy_Review_id_fkey" FOREIGN KEY ("Review_id") REFERENCES enrolment."Enrolment_Reviews"(id) ON DELETE CASCADE;


--
-- Name: Created_users created_users_learner_id_fkey; Type: FK CONSTRAINT; Schema: enrolment; Owner: -
--

ALTER TABLE ONLY enrolment."Created_users"
    ADD CONSTRAINT created_users_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES "Learner".learners(id) ON DELETE RESTRICT;


--
-- Name: Invitations Invitations_Account_id_fkey; Type: FK CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Invitations"
    ADD CONSTRAINT "Invitations_Account_id_fkey" FOREIGN KEY ("Account_id") REFERENCES login."Login_accounts"(id) ON DELETE CASCADE;


--
-- Name: Login_sessions Login_sessions_Account_id_fkey; Type: FK CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Login_sessions"
    ADD CONSTRAINT "Login_sessions_Account_id_fkey" FOREIGN KEY ("Account_id") REFERENCES login."Login_accounts"(id) ON DELETE CASCADE;


--
-- Name: Password_resets Password_resets_Account_id_fkey; Type: FK CONSTRAINT; Schema: login; Owner: -
--

ALTER TABLE ONLY login."Password_resets"
    ADD CONSTRAINT "Password_resets_Account_id_fkey" FOREIGN KEY ("Account_id") REFERENCES login."Login_accounts"(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
