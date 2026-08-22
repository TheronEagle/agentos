"""Example: build your own agent.

Workers execute tasks by resolving action keys against the capability
registry. Register handlers, spawn workers, wire them onto the A2A bus,
and they become discoverable, addressable operators.
"""

from __future__ import annotations

import asyncio

from agentos.agents.a2a import A2ABus
from agentos.agents.base import A2AMessage
from agentos.agents.worker import Worker
from agentos.core.registry import Registry
from agentos.models import Task


async def main() -> None:
    registry = Registry()
    bus = A2ABus()

    # 1. Give the domain some capabilities — typed, discoverable actions.
    async def translate(text: str, target_language: str) -> str:
        return f"[{target_language}] {text}"

    def word_count(text: str) -> int:
        return len(text.split())

    registry.register_tool(
        "lang.translate",
        "Translate text to a target language.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}, "target_language": {"type": "string"}},
            "required": ["text", "target_language"],
        },
        handler=translate,
        module="custom",
    )
    registry.register_tool(
        "text.word_count",
        "Count words in text.",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=word_count,
        module="custom",
    )

    # 2. Spawn specialised workers and put them on the bus.
    linguist = Worker(role="linguist", registry=registry, domain="lang")
    analyst = Worker(role="analyst", registry=registry, domain="text")
    coordinator = Worker(role="coordinator", registry=registry)
    for agent in (linguist, analyst, coordinator):
        bus.register(agent)

    print("registered capabilities:")
    for cap in registry.capabilities():
        print(f"  {cap.name} ({cap.kind})")

    # 3. Coordinator asks a specialist what it can do — A2A discovery.
    reply = await bus.request(coordinator.id, linguist.id, "translation work?")
    assert reply is not None
    print(f"\n{linguist.id} exposes {[c['name'] for c in reply['capabilities']]}")

    # 4. Delegate concrete tasks to each worker.
    t1 = Task(description="translate greeting", action="lang.translate", params={"text": "hello", "target_language": "ja"})
    t2 = Task(description="count words", action="text.word_count", params={"text": "agents do the clicking now"})
    r1 = await linguist.perform(t1)
    r2 = await analyst.perform(t2)
    print(f"\nlinguist → {r1['result']!r}  (task {t1.status})")
    print(f"analyst  → {r2['result']!r}  (task {t2.status})")

    # 5. Broadcast an announcement to every other agent.
    fanout = await bus.broadcast(linguist.id, "announce", {"new_capability": "lang.translate"})
    print(f"\nbroadcast reached {fanout} agents; history has {len(bus.history())} messages")


if __name__ == "__main__":
    asyncio.run(main())
