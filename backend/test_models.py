"""Schema behaviour: constraints, cascades and owner checks.

Runs on SQLite always, and on PostgreSQL too when the docker-compose database is reachable
(each run uses its own throwaway schema, so it never touches real data).
"""
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import db
import models as m


def _postgres_engine():
    url = db.database_url()
    if not url.startswith("postgresql"):
        return None
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect():
            pass
    except Exception:
        return None
    return engine


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request):
    if request.param == "sqlite":
        eng = create_engine("sqlite://")

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # SQLite ignores foreign keys unless asked
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        db.Base.metadata.create_all(eng)
        yield eng
        return

    base = _postgres_engine()
    if base is None:
        pytest.skip("PostgreSQL not reachable (run `docker compose up -d`)")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with base.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = base.execution_options(schema_translate_map={None: schema})
    db.Base.metadata.create_all(eng)
    yield eng
    with base.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def s(engine):
    with Session(engine) as session:
        yield session


def user(s, email="a@x.com"):
    u = m.User(email=email, password_hash="hash")
    s.add(u)
    s.commit()
    return u


def endpoint_model(s, u, name="m"):
    mod = m.Model(user_id=u.id, name=name, kind="endpoint", url="https://x/v1", model_name="gpt")
    s.add(mod)
    s.commit()
    return mod


def must_fail(s, *objs):
    s.add_all(objs)
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_email_unique_ignoring_case(s):
    user(s, "Alice@X.com")
    must_fail(s, m.User(email="alice@x.com", password_hash="h"))


def test_model_constraints(s):
    u = user(s)
    must_fail(s, m.Model(user_id=u.id, name="n", kind="bogus", url="u", model_name="x"))
    must_fail(s, m.Model(user_id=u.id, name="n", kind="endpoint", url=None, model_name="x"))
    must_fail(s, m.Model(user_id=uuid.uuid4(), name="n", kind="endpoint", url="u", model_name="x"))
    s.add(m.Model(user_id=u.id, name="uploaded", kind="upload"))  # uploads need no URL
    s.commit()


def test_log_source_and_report_score_constraints(s):
    u = user(s)
    must_fail(s, m.TrainingLog(user_id=u.id, filename="f", source="wandb", x_name="epoch", x=[], series={}))
    must_fail(s, m.Report(user_id=u.id, score=101, payload={}))
    must_fail(s, m.Report(user_id=u.id, score=-1, payload={}))
    s.add(m.Report(user_id=u.id, score=None, payload={"verdict": "no score yet"}))
    s.commit()


def test_json_round_trips(s):
    u = user(s)
    log = m.TrainingLog(user_id=u.id, filename="f", source="csv", x_name="epoch",
                        x=[0, 1], series={"loss": [1.0, None]}, val_loss="loss")
    s.add(log)
    s.commit()
    s.expire_all()
    got = s.get(m.TrainingLog, log.id)
    assert got.series == {"loss": [1.0, None]} and got.created_at is not None


def test_deleting_user_removes_everything_they_own(s):
    u, other = user(s), user(s, "b@x.com")
    mod = endpoint_model(s, u)
    sess = m.ChatSession(user_id=u.id, model_id=mod.id)
    s.add_all([m.TestSet(user_id=u.id, filename="t.csv", csv_text="a\n1"), sess])
    s.commit()
    s.add(m.ChatMessage(user_id=u.id, session_id=sess.id, position=0, role="user", content="hi"))
    s.add(m.Report(user_id=u.id, model_id=mod.id, payload={}))
    keep = endpoint_model(s, other, "theirs")
    s.commit()

    s.delete(u)
    s.commit()
    for table in (m.Model, m.TestSet, m.ChatSession, m.ChatMessage, m.Report):
        assert s.query(table).filter(table.user_id == u.id).count() == 0
    assert s.get(m.Model, keep.id) is not None  # other users untouched


def test_deleting_model_cascades_to_chats_but_keeps_reports(s):
    u = user(s)
    mod = endpoint_model(s, u)
    sess = m.ChatSession(user_id=u.id, model_id=mod.id)
    s.add(sess)
    s.commit()
    s.add(m.ChatMessage(user_id=u.id, session_id=sess.id, position=0, role="user", content="hi"))
    report = m.Report(user_id=u.id, model_id=mod.id, payload={"snapshot": 1})
    s.add(report)
    s.commit()

    s.query(m.Model).filter_by(id=mod.id).delete()
    s.commit()
    s.expire_all()
    assert s.query(m.ChatSession).count() == 0 and s.query(m.ChatMessage).count() == 0
    kept = s.get(m.Report, report.id)
    assert kept.model_id is None and kept.payload == {"snapshot": 1}


def test_chat_message_rules(s):
    u = user(s)
    sess = m.ChatSession(user_id=u.id, model_id=endpoint_model(s, u).id)
    s.add(sess)
    s.commit()
    msg = lambda **k: m.ChatMessage(user_id=u.id, session_id=sess.id, **{"position": 0, "role": "user", "content": "x", **k})  # noqa: E731
    must_fail(s, msg(role="robot"))
    s.add(msg())
    s.commit()
    must_fail(s, msg())  # position 0 is taken
    s.add(msg(position=1, role="assistant"))
    s.commit()
    s.expire_all()
    assert [x.position for x in s.get(m.ChatSession, sess.id).messages] == [0, 1]


def test_owner_checks_stop_cross_user_links(s):
    a, b = user(s, "a@x.com"), user(s, "b@x.com")
    a_model = endpoint_model(s, a)
    # b cannot open a chat on a's model...
    must_fail(s, m.ChatSession(user_id=b.id, model_id=a_model.id))
    # ...nor add messages to a's session.
    a_sess = m.ChatSession(user_id=a.id, model_id=a_model.id)
    s.add(a_sess)
    s.commit()
    must_fail(s, m.ChatMessage(user_id=b.id, session_id=a_sess.id, position=0, role="user", content="x"))
