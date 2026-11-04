"""The agent's tables in ops.sqlite."""

from scgraph.ops.store import OpsStore


def test_new_disruption_gets_its_thread_and_draft_status(tmp_path):
    store = OpsStore(tmp_path / "ops.sqlite")
    did = store.add_disruption({"name": "Fire"}, "ana", status="draft")
    assert store.disruption(did)["status"] == "draft"
    assert store.agent.thread_for(did)["title"] == "Fire"
    assert [d["id"] for d in store.disruptions()] == [did]
    assert store.disruptions(statuses=("active",)) == []
    store.update_disruption(did, status="archived")
    assert store.agent.thread_for(did)["status"] == "archived"
    store.delete_disruption(did)
    assert store.agent.thread_for(did) is None


def test_messages_plans_events_and_edit_blocks(tmp_path):
    store = OpsStore(tmp_path / "ops.sqlite")
    a = store.agent
    tid = a.create_thread("Question")
    m1 = a.add_message(tid, "ana", "person", [{"type": "text", "text": "hi"}], status="queued", profile="chat")
    m2 = a.add_message(tid, "ana", "person", [{"type": "text", "text": "again"}], status="queued", profile="chat")
    assert a.queued_threads() == [tid] and a.next_queued(tid)["id"] == m1
    a.finish_message(m1, None)
    assert a.next_queued(tid)["id"] == m2
    assert [m["id"] for m in a.messages(tid, last=1)] == [m2]
    did = store.add_disruption({"name": "Fire"}, "ana")
    p1 = a.add_plan(did, None, 5, [{"type": "spot_buy"}], "s", None, None)
    p2 = a.add_plan(did, None, 6, [], "nothing helps", None, None)
    assert (p1["version"], p2["version"]) == (1, 2) and a.current_plan(did)["id"] == p2["id"]
    eid = a.add_event("disruption.created", {"disruption_id": did}, "form", a.thread_for(did)["id"])
    assert a.events()[0]["payload"] == {"disruption_id": did} and a.event(eid)["source"] == "form"
    bid = a.add_edit_block(tid, [{"op": "set_field"}], [3], {"effects": []}, "user said so")
    a.decide_edit_block(bid, "applied", "ana", journal_id=9)
    block = a.edit_block(bid)
    assert block["status"] == "applied" and block["before"] == [3] and block["journal_id"] == 9
    a.update_thread(tid, needs="plan ready")
    assert a.threads(needs=True)[0]["id"] == tid
