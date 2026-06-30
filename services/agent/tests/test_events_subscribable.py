"""TDD: SubscribableEventPublisher 订阅式事件总线"""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_fanout_two_subscribers():
    from app.harness.events import SubscribableEventPublisher

    p = SubscribableEventPublisher()
    q1 = p.subscribe("run:1:events")
    q2 = p.subscribe("run:1:events")
    await p.publish("run:1:events", {"seq": 1, "type": "run_start"})
    assert (await asyncio.wait_for(q1.get(), 1))["seq"] == 1
    assert (await asyncio.wait_for(q2.get(), 1))["seq"] == 1


@pytest.mark.asyncio
async def test_ring_after_seq():
    from app.harness.events import SubscribableEventPublisher

    p = SubscribableEventPublisher()
    for i in range(1, 6):
        await p.publish("c", {"seq": i, "type": "x"})
    assert [e["seq"] for e in p.ring("c", after_seq=3)] == [4, 5]


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    from app.harness.events import SubscribableEventPublisher

    p = SubscribableEventPublisher()
    q = p.subscribe("c")
    p.unsubscribe("c", q)
    await p.publish("c", {"seq": 1})
    assert q.empty()
