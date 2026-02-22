--
-- PostgreSQL database dump
--

\restrict AgPZl4bAKCLeMclfrYB4X86apHzR1W3BWjcPqqpihLcxiEExnXP4LNXGWhjO5nG

-- Dumped from database version 16.11 (Ubuntu 16.11-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.11 (Ubuntu 16.11-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: citext; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public;


--
-- Name: EXTENSION citext; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION citext IS 'data type for case-insensitive character strings';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: postgis; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;


--
-- Name: EXTENSION postgis; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types and functions';


--
-- Name: incident_state; Type: TYPE; Schema: public; Owner: oil
--

CREATE TYPE public.incident_state AS ENUM (
    'Detection',
    'Triage',
    'VerificationTasked',
    'VerificationResult',
    'ResponsePlanning',
    'ResponseActive',
    'ResponseComplete',
    'Closed'
);


ALTER TYPE public.incident_state OWNER TO oil;

--
-- Name: responder_type; Type: TYPE; Schema: public; Owner: oil
--

CREATE TYPE public.responder_type AS ENUM (
    'drone',
    'human'
);


ALTER TYPE public.responder_type OWNER TO oil;

--
-- Name: task_status; Type: TYPE; Schema: public; Owner: oil
--

CREATE TYPE public.task_status AS ENUM (
    'queued',
    'assigned',
    'in_progress',
    'blocked',
    'done',
    'canceled'
);


ALTER TYPE public.task_status OWNER TO oil;

--
-- Name: verification_outcome; Type: TYPE; Schema: public; Owner: oil
--

CREATE TYPE public.verification_outcome AS ENUM (
    'confirmed',
    'refuted',
    'unsure'
);


ALTER TYPE public.verification_outcome OWNER TO oil;

--
-- Name: enforce_incident_fsm(); Type: FUNCTION; Schema: public; Owner: oil
--

CREATE FUNCTION public.enforce_incident_fsm() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NEW.state <> OLD.state THEN
    IF NOT EXISTS (
      SELECT 1 FROM incident_transitions
      WHERE from_state = OLD.state AND to_state = NEW.state
    ) THEN
      RAISE EXCEPTION 'Invalid transition: % -> %', OLD.state, NEW.state;
    END IF;

    IF NEW.state IN ('Closed','ResponseComplete') AND NEW.closed_at IS NULL THEN
      NEW.closed_at := now();
    END IF;

    INSERT INTO incident_events (incident_id, actor_user, from_state, to_state, event_type, details)
    VALUES (NEW.id, NULLIF(current_setting('app.current_user', true), '')::uuid, OLD.state, NEW.state, 'state_change', '{}'::jsonb);
  END IF;

  NEW.updated_at := now();
  RETURN NEW;
END;
$$;


ALTER FUNCTION public.enforce_incident_fsm() OWNER TO oil;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: assets; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.assets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    type public.responder_type NOT NULL,
    name text NOT NULL,
    callsign text,
    status text DEFAULT 'available'::text NOT NULL,
    home_base public.geometry(Point,4326),
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.assets OWNER TO oil;

--
-- Name: attachments; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.attachments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid,
    mission_id uuid,
    task_id uuid,
    uploaded_by uuid,
    url text NOT NULL,
    mime_type text,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.attachments OWNER TO oil;

--
-- Name: detections; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.detections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    model_name text NOT NULL,
    model_version text NOT NULL,
    image_id text NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    confidence real NOT NULL,
    polygon public.geometry(Polygon,4326) NOT NULL,
    area_sqkm double precision GENERATED ALWAYS AS ((public.st_area((polygon)::public.geography) / ('1000000'::numeric)::double precision)) STORED,
    bbox public.geometry(Polygon,4326) GENERATED ALWAYS AS (public.st_envelope(polygon)) STORED,
    extra jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.detections OWNER TO oil;

--
-- Name: incident_detections; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.incident_detections (
    incident_id uuid NOT NULL,
    detection_id uuid NOT NULL
);


ALTER TABLE public.incident_detections OWNER TO oil;

--
-- Name: incident_events; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.incident_events (
    id bigint NOT NULL,
    incident_id uuid NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor_user uuid,
    actor_asset uuid,
    from_state public.incident_state,
    to_state public.incident_state,
    event_type text NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL
);


ALTER TABLE public.incident_events OWNER TO oil;

--
-- Name: incident_events_id_seq; Type: SEQUENCE; Schema: public; Owner: oil
--

CREATE SEQUENCE public.incident_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.incident_events_id_seq OWNER TO oil;

--
-- Name: incident_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oil
--

ALTER SEQUENCE public.incident_events_id_seq OWNED BY public.incident_events.id;


--
-- Name: incident_sites; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.incident_sites (
    incident_id uuid NOT NULL,
    site_id uuid NOT NULL,
    relation_type text NOT NULL,
    distance_m double precision,
    buffer_m integer,
    snapshot jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT incident_sites_relation_type_check CHECK ((relation_type = ANY (ARRAY['nearest'::text, 'intersects'::text, 'within_radius'::text, 'within_10km'::text])))
);


ALTER TABLE public.incident_sites OWNER TO oil;

--
-- Name: incident_transitions; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.incident_transitions (
    from_state public.incident_state NOT NULL,
    to_state public.incident_state NOT NULL
);


ALTER TABLE public.incident_transitions OWNER TO oil;

--
-- Name: incidents; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.incidents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    title text NOT NULL,
    state public.incident_state DEFAULT 'Detection'::public.incident_state NOT NULL,
    priority integer DEFAULT 3 NOT NULL,
    centroid public.geometry(Point,4326) NOT NULL,
    footprint public.geometry(Polygon,4326),
    est_area_sqkm double precision,
    detection_source text,
    raised_by uuid,
    verification public.verification_outcome,
    verified_by uuid,
    verified_at timestamp with time zone,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    dist_shore_km double precision,
    verification_at timestamp with time zone,
    verification_by text,
    nearest_port_id uuid,
    nearest_port_distance_m double precision,
    nearest_desal_id uuid,
    nearest_desal_distance_m double precision,
    nearest_protected_id uuid,
    nearest_protected_distance_m double precision
);


ALTER TABLE public.incidents OWNER TO oil;

--
-- Name: missions; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.missions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    incident_id uuid NOT NULL,
    purpose text NOT NULL,
    tier_level integer,
    assigned_to uuid,
    status text DEFAULT 'planned'::text NOT NULL,
    route public.geometry(LineString,4326),
    waypoint public.geometry(Point,4326),
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.missions OWNER TO oil;

--
-- Name: orgs; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.orgs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.orgs OWNER TO oil;

--
-- Name: response_actions; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.response_actions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    tier_id uuid NOT NULL,
    name text NOT NULL,
    description text,
    plan_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.response_actions OWNER TO oil;

--
-- Name: response_plans; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.response_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid NOT NULL,
    tier_id uuid NOT NULL,
    decided_by uuid,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    auto_generated boolean DEFAULT true NOT NULL,
    rationale text,
    snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    name text,
    description text,
    plan_json jsonb,
    response_action_id uuid
);


ALTER TABLE public.response_plans OWNER TO oil;

--
-- Name: response_rules; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.response_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    min_probability real NOT NULL,
    max_probability real NOT NULL,
    min_consequence integer NOT NULL,
    max_consequence integer NOT NULL,
    tier_id uuid NOT NULL,
    actions_template jsonb DEFAULT '{}'::jsonb NOT NULL,
    min_area_km2 double precision,
    max_area_km2 double precision,
    min_dist_shore_km double precision,
    max_dist_shore_km double precision,
    CONSTRAINT response_rules_max_probability_check CHECK (((max_probability >= (0)::double precision) AND (max_probability <= (1)::double precision))),
    CONSTRAINT response_rules_min_probability_check CHECK (((min_probability >= (0)::double precision) AND (min_probability <= (1)::double precision)))
);


ALTER TABLE public.response_rules OWNER TO oil;

--
-- Name: response_tiers; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.response_tiers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    name text NOT NULL,
    tier_order integer NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tier_level integer
);


ALTER TABLE public.response_tiers OWNER TO oil;

--
-- Name: shorelines; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.shorelines (
    gid integer NOT NULL,
    featurecla character varying(80),
    scalerank double precision,
    min_zoom double precision,
    geom public.geometry(MultiLineString,4326)
);


ALTER TABLE public.shorelines OWNER TO oil;

--
-- Name: shorelines_gid_seq; Type: SEQUENCE; Schema: public; Owner: oil
--

CREATE SEQUENCE public.shorelines_gid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.shorelines_gid_seq OWNER TO oil;

--
-- Name: shorelines_gid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oil
--

ALTER SEQUENCE public.shorelines_gid_seq OWNED BY public.shorelines.gid;


--
-- Name: sites; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.sites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_type text NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    source_id text,
    props jsonb DEFAULT '{}'::jsonb NOT NULL,
    geom public.geometry(Geometry,4326),
    center public.geometry(Point,4326),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sites_site_type_check CHECK ((site_type = ANY (ARRAY['port'::text, 'desalination'::text, 'protected_area'::text])))
);


ALTER TABLE public.sites OWNER TO oil;

--
-- Name: tasks; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid NOT NULL,
    mission_id uuid,
    type text NOT NULL,
    status public.task_status DEFAULT 'queued'::public.task_status NOT NULL,
    assigned_asset uuid,
    instructions text,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.tasks OWNER TO oil;

--
-- Name: users; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    email public.citext NOT NULL,
    display_name text,
    role text DEFAULT 'operator'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    disabled_at timestamp with time zone
);


ALTER TABLE public.users OWNER TO oil;

--
-- Name: verification_results; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.verification_results (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid NOT NULL,
    client_id text,
    outcome public.verification_outcome NOT NULL,
    notes text,
    evidence jsonb,
    decided_by text,
    decided_at timestamp with time zone DEFAULT now(),
    arrival_at timestamp with time zone,
    arrived_distance_m double precision
);


ALTER TABLE public.verification_results OWNER TO oil;

--
-- Name: verification_tasks; Type: TABLE; Schema: public; Owner: oil
--

CREATE TABLE public.verification_tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid NOT NULL,
    drone_id text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    pattern text,
    agl_m numeric,
    speed_mps numeric,
    trig_every_m numeric,
    polygon public.geometry,
    options jsonb,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    result text,
    arrived_at timestamp with time zone,
    arrived_distance_m numeric
);


ALTER TABLE public.verification_tasks OWNER TO oil;

--
-- Name: incident_events id; Type: DEFAULT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_events ALTER COLUMN id SET DEFAULT nextval('public.incident_events_id_seq'::regclass);


--
-- Name: shorelines gid; Type: DEFAULT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.shorelines ALTER COLUMN gid SET DEFAULT nextval('public.shorelines_gid_seq'::regclass);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: attachments attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_pkey PRIMARY KEY (id);


--
-- Name: detections detections_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.detections
    ADD CONSTRAINT detections_pkey PRIMARY KEY (id);


--
-- Name: incident_detections incident_detections_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_detections
    ADD CONSTRAINT incident_detections_pkey PRIMARY KEY (incident_id, detection_id);


--
-- Name: incident_events incident_events_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_events
    ADD CONSTRAINT incident_events_pkey PRIMARY KEY (id);


--
-- Name: incident_sites incident_sites_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_sites
    ADD CONSTRAINT incident_sites_pkey PRIMARY KEY (incident_id, site_id, relation_type);


--
-- Name: incident_transitions incident_transitions_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_transitions
    ADD CONSTRAINT incident_transitions_pkey PRIMARY KEY (from_state, to_state);


--
-- Name: incidents incidents_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT incidents_pkey PRIMARY KEY (id);


--
-- Name: missions missions_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.missions
    ADD CONSTRAINT missions_pkey PRIMARY KEY (id);


--
-- Name: orgs orgs_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.orgs
    ADD CONSTRAINT orgs_pkey PRIMARY KEY (id);


--
-- Name: response_actions response_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_actions
    ADD CONSTRAINT response_actions_pkey PRIMARY KEY (id);


--
-- Name: response_plans response_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_plans
    ADD CONSTRAINT response_plans_pkey PRIMARY KEY (id);


--
-- Name: response_rules response_rules_org_id_min_probability_max_probability_min_c_key; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_rules
    ADD CONSTRAINT response_rules_org_id_min_probability_max_probability_min_c_key UNIQUE (org_id, min_probability, max_probability, min_consequence, max_consequence);


--
-- Name: response_rules response_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_rules
    ADD CONSTRAINT response_rules_pkey PRIMARY KEY (id);


--
-- Name: response_tiers response_tiers_org_id_level_key; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_tiers
    ADD CONSTRAINT response_tiers_org_id_level_key UNIQUE (org_id, tier_order);


--
-- Name: response_tiers response_tiers_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_tiers
    ADD CONSTRAINT response_tiers_pkey PRIMARY KEY (id);


--
-- Name: shorelines shorelines_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.shorelines
    ADD CONSTRAINT shorelines_pkey PRIMARY KEY (gid);


--
-- Name: sites sites_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_pkey PRIMARY KEY (id);


--
-- Name: sites sites_provider_source_uk; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_provider_source_uk UNIQUE (provider, source_id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: verification_results verification_results_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.verification_results
    ADD CONSTRAINT verification_results_pkey PRIMARY KEY (id);


--
-- Name: verification_tasks verification_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.verification_tasks
    ADD CONSTRAINT verification_tasks_pkey PRIMARY KEY (id);


--
-- Name: detections_gix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX detections_gix ON public.detections USING gist (polygon);


--
-- Name: detections_time_ix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX detections_time_ix ON public.detections USING btree (captured_at);


--
-- Name: incident_events_ix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incident_events_ix ON public.incident_events USING btree (incident_id, at);


--
-- Name: incident_sites_incident_idx; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incident_sites_incident_idx ON public.incident_sites USING btree (incident_id);


--
-- Name: incident_sites_rel_idx; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incident_sites_rel_idx ON public.incident_sites USING btree (relation_type);


--
-- Name: incident_sites_site_idx; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incident_sites_site_idx ON public.incident_sites USING btree (site_id);


--
-- Name: incidents_centroid_gix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incidents_centroid_gix ON public.incidents USING gist (centroid);


--
-- Name: incidents_footprint_gix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incidents_footprint_gix ON public.incidents USING gist (footprint);


--
-- Name: incidents_priority_ix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incidents_priority_ix ON public.incidents USING btree (priority, updated_at);


--
-- Name: incidents_state_ix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX incidents_state_ix ON public.incidents USING btree (state);


--
-- Name: missions_incident_ix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX missions_incident_ix ON public.missions USING btree (incident_id);


--
-- Name: missions_waypoint_gix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX missions_waypoint_gix ON public.missions USING gist (waypoint);


--
-- Name: response_actions_org_tier_name_uk; Type: INDEX; Schema: public; Owner: oil
--

CREATE UNIQUE INDEX response_actions_org_tier_name_uk ON public.response_actions USING btree (org_id, tier_id, name);


--
-- Name: response_tiers_org_level_key; Type: INDEX; Schema: public; Owner: oil
--

CREATE UNIQUE INDEX response_tiers_org_level_key ON public.response_tiers USING btree (org_id, tier_order);


--
-- Name: shorelines_geom_idx; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX shorelines_geom_idx ON public.shorelines USING gist (geom);


--
-- Name: sites_center_gix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX sites_center_gix ON public.sites USING gist (center);


--
-- Name: sites_geom_gix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX sites_geom_gix ON public.sites USING gist (geom);


--
-- Name: sites_props_gin; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX sites_props_gin ON public.sites USING gin (props);


--
-- Name: sites_provider_idx; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX sites_provider_idx ON public.sites USING btree (provider);


--
-- Name: sites_type_idx; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX sites_type_idx ON public.sites USING btree (site_type);


--
-- Name: tasks_incident_ix; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX tasks_incident_ix ON public.tasks USING btree (incident_id, status);


--
-- Name: verification_tasks_incident_idx; Type: INDEX; Schema: public; Owner: oil
--

CREATE INDEX verification_tasks_incident_idx ON public.verification_tasks USING btree (incident_id);


--
-- Name: incidents trg_incident_fsm; Type: TRIGGER; Schema: public; Owner: oil
--

CREATE TRIGGER trg_incident_fsm BEFORE UPDATE ON public.incidents FOR EACH ROW EXECUTE FUNCTION public.enforce_incident_fsm();


--
-- Name: assets assets_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: attachments attachments_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: attachments attachments_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.missions(id) ON DELETE SET NULL;


--
-- Name: attachments attachments_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON DELETE SET NULL;


--
-- Name: attachments attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: detections detections_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.detections
    ADD CONSTRAINT detections_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: incident_detections incident_detections_detection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_detections
    ADD CONSTRAINT incident_detections_detection_id_fkey FOREIGN KEY (detection_id) REFERENCES public.detections(id) ON DELETE CASCADE;


--
-- Name: incident_detections incident_detections_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_detections
    ADD CONSTRAINT incident_detections_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: incident_events incident_events_actor_asset_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_events
    ADD CONSTRAINT incident_events_actor_asset_fkey FOREIGN KEY (actor_asset) REFERENCES public.assets(id);


--
-- Name: incident_events incident_events_actor_user_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_events
    ADD CONSTRAINT incident_events_actor_user_fkey FOREIGN KEY (actor_user) REFERENCES public.users(id);


--
-- Name: incident_events incident_events_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_events
    ADD CONSTRAINT incident_events_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: incident_sites incident_sites_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_sites
    ADD CONSTRAINT incident_sites_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: incident_sites incident_sites_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incident_sites
    ADD CONSTRAINT incident_sites_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE CASCADE;


--
-- Name: incidents incidents_nearest_desal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT incidents_nearest_desal_id_fkey FOREIGN KEY (nearest_desal_id) REFERENCES public.sites(id);


--
-- Name: incidents incidents_nearest_port_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT incidents_nearest_port_id_fkey FOREIGN KEY (nearest_port_id) REFERENCES public.sites(id);


--
-- Name: incidents incidents_nearest_protected_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT incidents_nearest_protected_id_fkey FOREIGN KEY (nearest_protected_id) REFERENCES public.sites(id);


--
-- Name: incidents incidents_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT incidents_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: incidents incidents_raised_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT incidents_raised_by_fkey FOREIGN KEY (raised_by) REFERENCES public.users(id);


--
-- Name: incidents incidents_verified_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT incidents_verified_by_fkey FOREIGN KEY (verified_by) REFERENCES public.users(id);


--
-- Name: missions missions_assigned_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.missions
    ADD CONSTRAINT missions_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES public.assets(id);


--
-- Name: missions missions_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.missions
    ADD CONSTRAINT missions_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: missions missions_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.missions
    ADD CONSTRAINT missions_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: response_plans response_plans_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_plans
    ADD CONSTRAINT response_plans_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: response_plans response_plans_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_plans
    ADD CONSTRAINT response_plans_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: response_rules response_rules_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_rules
    ADD CONSTRAINT response_rules_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: response_rules response_rules_tier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_rules
    ADD CONSTRAINT response_rules_tier_id_fkey FOREIGN KEY (tier_id) REFERENCES public.response_tiers(id) ON DELETE CASCADE;


--
-- Name: response_tiers response_tiers_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.response_tiers
    ADD CONSTRAINT response_tiers_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: tasks tasks_assigned_asset_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_assigned_asset_fkey FOREIGN KEY (assigned_asset) REFERENCES public.assets(id);


--
-- Name: tasks tasks_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: tasks tasks_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.missions(id) ON DELETE SET NULL;


--
-- Name: users users_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: verification_results verification_results_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.verification_results
    ADD CONSTRAINT verification_results_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: verification_tasks verification_tasks_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oil
--

ALTER TABLE ONLY public.verification_tasks
    ADD CONSTRAINT verification_tasks_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict AgPZl4bAKCLeMclfrYB4X86apHzR1W3BWjcPqqpihLcxiEExnXP4LNXGWhjO5nG

