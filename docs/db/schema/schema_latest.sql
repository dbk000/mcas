--
-- PostgreSQL database cluster dump
--

\restrict 2jdS8rbv2V9k7jUZGF0BYgkwegr7MgKxmft3dOxREjgrmUijpsXzcTrsaT7Fxv0

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

--
-- Roles
--

CREATE ROLE trading;
ALTER ROLE trading WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;

--
-- User Configurations
--








\unrestrict 2jdS8rbv2V9k7jUZGF0BYgkwegr7MgKxmft3dOxREjgrmUijpsXzcTrsaT7Fxv0

--
-- Databases
--

--
-- Database "template1" dump
--

\connect template1

--
-- PostgreSQL database dump
--

\restrict PCwpZO67BFgpLKb5Fg4TVcQa3KgqdaQRRPGcMAaJGT284vriaV9NgCREZXJrQpV

-- Dumped from database version 16.11 (Debian 16.11-1.pgdg13+1)
-- Dumped by pg_dump version 16.11 (Debian 16.11-1.pgdg13+1)

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
-- PostgreSQL database dump complete
--

\unrestrict PCwpZO67BFgpLKb5Fg4TVcQa3KgqdaQRRPGcMAaJGT284vriaV9NgCREZXJrQpV

--
-- Database "postgres" dump
--

\connect postgres

--
-- PostgreSQL database dump
--

\restrict 9i2MdvN4SWiibeD7gVzgcVdvleGOBbt7bbOU4qDkAO8UCejMHcx6lAXLDCFDltw

-- Dumped from database version 16.11 (Debian 16.11-1.pgdg13+1)
-- Dumped by pg_dump version 16.11 (Debian 16.11-1.pgdg13+1)

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
-- PostgreSQL database dump complete
--

\unrestrict 9i2MdvN4SWiibeD7gVzgcVdvleGOBbt7bbOU4qDkAO8UCejMHcx6lAXLDCFDltw

--
-- Database "trading" dump
--

--
-- PostgreSQL database dump
--

\restrict CawOpWotYanCB2oRGof0m0eifqfiJjZwqTEkw85otd4oSQZO1o00dZNmCgrJnsw

-- Dumped from database version 16.11 (Debian 16.11-1.pgdg13+1)
-- Dumped by pg_dump version 16.11 (Debian 16.11-1.pgdg13+1)

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
-- Name: trading; Type: DATABASE; Schema: -; Owner: trading
--

CREATE DATABASE trading WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE trading OWNER TO trading;

\unrestrict CawOpWotYanCB2oRGof0m0eifqfiJjZwqTEkw85otd4oSQZO1o00dZNmCgrJnsw
\connect trading
\restrict CawOpWotYanCB2oRGof0m0eifqfiJjZwqTEkw85otd4oSQZO1o00dZNmCgrJnsw

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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: opportunities; Type: TABLE; Schema: public; Owner: trading
--

CREATE TABLE public.opportunities (
    id integer NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    updated_ts timestamp with time zone DEFAULT now() NOT NULL,
    signal_id integer,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    setup_id text NOT NULL,
    decision text NOT NULL,
    score double precision DEFAULT 0 NOT NULL,
    reason text,
    payload_json text,
    status text DEFAULT 'active'::text NOT NULL
);


ALTER TABLE public.opportunities OWNER TO trading;

--
-- Name: opportunities_id_seq; Type: SEQUENCE; Schema: public; Owner: trading
--

CREATE SEQUENCE public.opportunities_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.opportunities_id_seq OWNER TO trading;

--
-- Name: opportunities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: trading
--

ALTER SEQUENCE public.opportunities_id_seq OWNED BY public.opportunities.id;


--
-- Name: signal_queue; Type: TABLE; Schema: public; Owner: trading
--

CREATE TABLE public.signal_queue (
    id integer NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    setup_id text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    result_json text,
    updated_ts timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.signal_queue OWNER TO trading;

--
-- Name: signal_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: trading
--

CREATE SEQUENCE public.signal_queue_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.signal_queue_id_seq OWNER TO trading;

--
-- Name: signal_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: trading
--

ALTER SEQUENCE public.signal_queue_id_seq OWNED BY public.signal_queue.id;


--
-- Name: worker_heartbeat; Type: TABLE; Schema: public; Owner: trading
--

CREATE TABLE public.worker_heartbeat (
    id integer NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.worker_heartbeat OWNER TO trading;

--
-- Name: worker_heartbeat_id_seq; Type: SEQUENCE; Schema: public; Owner: trading
--

CREATE SEQUENCE public.worker_heartbeat_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.worker_heartbeat_id_seq OWNER TO trading;

--
-- Name: worker_heartbeat_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: trading
--

ALTER SEQUENCE public.worker_heartbeat_id_seq OWNED BY public.worker_heartbeat.id;


--
-- Name: opportunities id; Type: DEFAULT; Schema: public; Owner: trading
--

ALTER TABLE ONLY public.opportunities ALTER COLUMN id SET DEFAULT nextval('public.opportunities_id_seq'::regclass);


--
-- Name: signal_queue id; Type: DEFAULT; Schema: public; Owner: trading
--

ALTER TABLE ONLY public.signal_queue ALTER COLUMN id SET DEFAULT nextval('public.signal_queue_id_seq'::regclass);


--
-- Name: worker_heartbeat id; Type: DEFAULT; Schema: public; Owner: trading
--

ALTER TABLE ONLY public.worker_heartbeat ALTER COLUMN id SET DEFAULT nextval('public.worker_heartbeat_id_seq'::regclass);


--
-- Name: opportunities opportunities_pkey; Type: CONSTRAINT; Schema: public; Owner: trading
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_pkey PRIMARY KEY (id);


--
-- Name: signal_queue signal_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: trading
--

ALTER TABLE ONLY public.signal_queue
    ADD CONSTRAINT signal_queue_pkey PRIMARY KEY (id);


--
-- Name: worker_heartbeat worker_heartbeat_pkey; Type: CONSTRAINT; Schema: public; Owner: trading
--

ALTER TABLE ONLY public.worker_heartbeat
    ADD CONSTRAINT worker_heartbeat_pkey PRIMARY KEY (id);


--
-- Name: idx_opps_status_score_ts; Type: INDEX; Schema: public; Owner: trading
--

CREATE INDEX idx_opps_status_score_ts ON public.opportunities USING btree (status, score DESC, updated_ts DESC);


--
-- Name: uq_opps_signal_id; Type: INDEX; Schema: public; Owner: trading
--

CREATE UNIQUE INDEX uq_opps_signal_id ON public.opportunities USING btree (signal_id);


--
-- Name: opportunities opportunities_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: trading
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES public.signal_queue(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict CawOpWotYanCB2oRGof0m0eifqfiJjZwqTEkw85otd4oSQZO1o00dZNmCgrJnsw

--
-- PostgreSQL database cluster dump complete
--

