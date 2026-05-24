from agent_eval_orchestrator.controller.provisioner import (
    STEP_LABELS,
    initial_steps_for_mode,
    set_step_status,
)


def test_initial_steps_fresh():
    steps = initial_steps_for_mode("fresh")
    assert [step["id"] for step in steps] == [
        "validate_ssh",
        "bootstrap",
        "verify_layout",
        "establish_tunnel",
        "start_daemon",
        "wait_register",
    ]
    assert all(step["status"] == "pending" for step in steps)


def test_initial_steps_join():
    steps = initial_steps_for_mode("join")
    assert [step["id"] for step in steps] == [
        "validate_ssh",
        "verify_layout",
        "establish_tunnel",
        "start_daemon",
        "wait_register",
    ]


def test_set_step_status():
    steps = initial_steps_for_mode("join")
    updated = set_step_status(steps, "verify_layout", "failed")
    verify = next(step for step in updated if step["id"] == "verify_layout")
    assert verify["status"] == "failed"
    assert verify["label"] == STEP_LABELS["verify_layout"]
