from app.model import repo_templates as repo


def test_create_and_get(session):
    tpl = repo.create_template(session, owner="alice", name="t1", dataset_ref="d/x",
                               executor_kind="harbor", executor_config={"k": 1})
    session.commit()
    assert tpl.template_id.startswith("tpl-")
    got = repo.get_template(session, tpl.template_id)
    assert got is not None and got.name == "t1" and got.executor_config["k"] == 1


def test_list_filters_by_owner(session):
    repo.create_template(session, owner="alice", name="a", dataset_ref="d", executor_kind="harbor", executor_config={})
    repo.create_template(session, owner="bob", name="b", dataset_ref="d", executor_kind="harbor", executor_config={})
    session.commit()
    owners = {t.name for t in repo.list_templates(session, owner="alice")}
    assert owners == {"a"}
    assert len(repo.list_templates(session)) == 2
