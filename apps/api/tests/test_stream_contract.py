"""Pin the wire contract so a change forces an update to the TS mirror."""

from __future__ import annotations

from micracode_core.schemas.stream import (
    ErrorEvent,
    FileDeleteEvent,
    FileWriteEvent,
    MessageDeltaEvent,
    ShellExecEvent,
    StatusEvent,
)


def test_all_event_types_round_trip() -> None:
    events = [
        MessageDeltaEvent(content="hello"),
        FileWriteEvent(path="app/page.tsx", content="export default () => null;"),
        FileDeleteEvent(path="obsolete.ts"),
        ShellExecEvent(command="npm install"),
        ShellExecEvent(command="npm run dev", cwd="/app"),
        StatusEvent(stage="planning"),
        StatusEvent(stage="generating", note="writing files"),
        StatusEvent(stage="done"),
        ErrorEvent(message="boom", recoverable=True),
    ]

    for event in events:
        data = event.model_dump(mode="json")
        # Discriminator must be present and a string literal.
        assert isinstance(data["type"], str)
        assert data["type"] == event.type
